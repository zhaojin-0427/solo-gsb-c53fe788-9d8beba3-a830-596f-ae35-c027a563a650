"""航标灯质可辨性本地 Web 预检台 —— FastAPI 入口。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .engine import analyze, invert, revise
from .schemas import (
    AnalysisCreate,
    InversionCreate,
    LightsPayload,
    RevisionApply,
    RevisionCreate,
    SettingsPayload,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = os.environ.get("DB_PATH", str(BASE_DIR / "data" / "beacon.db"))

app = FastAPI(
    title="航标灯质可辨性预检台",
    description="穷举灯对全部独立起始相位，求最短可辨观测窗口",
    version="1.0.0",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db(DB_PATH)
    yield


app.router.lifespan_context = lifespan


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
def get_state() -> dict[str, object]:
    with db.connect(DB_PATH) as conn:
        return {
            "settings": db.get_settings(conn),
            "lights": db.list_lights(conn),
        }


@app.put("/api/lights")
def put_lights(payload: LightsPayload) -> dict[str, object]:
    items = [{"name": x.name, "pattern": x.pattern} for x in payload.lights]
    with db.connect(DB_PATH) as conn:
        lights = db.replace_lights(conn, items)
    return {"lights": lights}


@app.put("/api/settings")
def put_settings(payload: SettingsPayload) -> dict[str, object]:
    with db.connect(DB_PATH) as conn:
        settings = db.update_settings(conn, payload.w, payload.k)
    return {"settings": settings}


@app.get("/api/analyses")
def get_analyses() -> dict[str, object]:
    with db.connect(DB_PATH) as conn:
        return {"analyses": db.list_analyses(conn)}


@app.post("/api/analyses", status_code=201)
def post_analysis(payload: AnalysisCreate) -> dict[str, object]:
    # 以“保存分析”时刻的灯串与参数为快照；分析可能耗时数秒，在线程池中执行
    with db.connect(DB_PATH) as conn:
        lights = db.list_lights(conn)
        settings = db.get_settings(conn)
    patterns = [item["pattern"] for item in lights]
    result = analyze(patterns, settings["w"], settings["k"])
    snapshot = [{"name": item["name"], "pattern": item["pattern"]} for item in lights]
    with db.connect(DB_PATH) as conn:
        record = db.create_analysis(
            conn, payload.note.strip(), settings["w"], settings["k"], snapshot, result
        )
    return record


@app.get("/api/analyses/{analysis_id}")
def get_analysis(analysis_id: int) -> dict[str, object]:
    with db.connect(DB_PATH) as conn:
        record = db.get_analysis(conn, analysis_id)
    if record is None:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    return record


@app.delete("/api/analyses/{analysis_id}", status_code=204)
def remove_analysis(analysis_id: int) -> JSONResponse:
    with db.connect(DB_PATH) as conn:
        ok = db.delete_analysis(conn, analysis_id)
    if not ok:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    return JSONResponse(status_code=204, content=None)


@app.get("/api/inversions")
def get_inversions() -> dict[str, object]:
    with db.connect(DB_PATH) as conn:
        return {"inversions": db.list_inversions(conn)}


@app.post("/api/analyses/{analysis_id}/inversions", status_code=201)
def post_inversion(analysis_id: int, payload: InversionCreate) -> dict[str, object]:
    # 以所选已保存分析的冻结快照为反演对象；快照随记录再冻结一份
    with db.connect(DB_PATH) as conn:
        analysis = db.get_analysis(conn, analysis_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="分析快照不存在，无法反演")
        snapshot = analysis["snapshot"]
        w = analysis["w"]
    if len(payload.observation) > w:
        raise HTTPException(
            status_code=422,
            detail=f"观测串长度 {len(payload.observation)} 超过该快照的观测窗口上限 W={w}",
        )
    result = invert(snapshot, payload.observation, payload.e, w)
    with db.connect(DB_PATH) as conn:
        record = db.create_inversion(
            conn, payload.note.strip(), analysis_id, w,
            payload.observation, payload.e, snapshot, result,
        )
    return record


@app.get("/api/inversions/{inversion_id}")
def get_inversion(inversion_id: int) -> dict[str, object]:
    with db.connect(DB_PATH) as conn:
        record = db.get_inversion(conn, inversion_id)
    if record is None:
        raise HTTPException(status_code=404, detail="反演记录不存在")
    return record


@app.delete("/api/inversions/{inversion_id}", status_code=204)
def remove_inversion(inversion_id: int) -> JSONResponse:
    with db.connect(DB_PATH) as conn:
        ok = db.delete_inversion(conn, inversion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="反演记录不存在")
    return JSONResponse(status_code=204, content=None)


# ---------- 灯串修订建议 ----------

def _load_confused_analysis(analysis_id: int) -> dict[str, object]:
    """取冻结分析并确认其在 W 位不可辨；否则 404/409。"""
    with db.connect(DB_PATH) as conn:
        analysis = db.get_analysis(conn, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="分析快照不存在或已删除，无法修订")
    if analysis["result"].get("solvable"):
        raise HTTPException(
            status_code=409,
            detail="该冻结分析在 W 位本就可辨，无需灯串修订；请选择一条「W 内无解」的分析。",
        )
    return analysis


def _recheck_revision(
    analysis: dict[str, object], light_index: int, flip_positions: list[int]
) -> tuple[str, dict[str, object]]:
    """服务端独立复核：按翻转位置重算修订灯串并重新搜索确认。

    返回 (修订后灯串, 复核结果)。应用前不信任客户端传来的灯串/全局距离，
    一律以冻结快照为唯一事实来源重新计算。
    """
    snapshot = analysis["snapshot"]
    w, k = analysis["w"], analysis["k"]
    if not (0 <= light_index < len(snapshot)):
        raise HTTPException(status_code=422, detail="被修订灯序号超出快照范围")
    original = snapshot[light_index]["pattern"]
    if any(not (0 <= p < len(original)) for p in flip_positions):
        raise HTTPException(status_code=422, detail="翻转位置超出该灯串长度")
    chars = list(original)
    for p in flip_positions:
        chars[p] = "1" if chars[p] == "0" else "0"
    new_pattern = "".join(chars)
    if "0" not in new_pattern or "1" not in new_pattern:
        raise HTTPException(status_code=422, detail="修订后灯串必须同时包含 0 和 1")
    check = revise(snapshot, w, k, light_index, 2)
    return new_pattern, check


@app.post("/api/analyses/{analysis_id}/revision-suggest")
def post_revision_suggest(analysis_id: int, payload: RevisionCreate) -> dict[str, object]:
    # 读取快照灯串、W、k 交给搜索引擎；搜索引擎穷举只改该灯、翻转 1~max_flips
    # 位且修订后仍含 0、1 的全部候选，按（翻转数, 全局最小距离, 位置序列）裁决。
    analysis = _load_confused_analysis(analysis_id)
    snapshot = analysis["snapshot"]
    if not (0 <= payload.light_index < len(snapshot)):
        raise HTTPException(status_code=422, detail="指定的灯序号超出该快照的灯数范围")
    result = revise(
        snapshot, analysis["w"], analysis["k"],
        payload.light_index, payload.max_flips,
    )
    # 无解也是正常 200 响应（found=False），明确提示且不改任何工作区数据
    return {"analysis_id": analysis_id, **result}


@app.post("/api/analyses/{analysis_id}/revision-apply")
def post_revision_apply(analysis_id: int, payload: RevisionApply) -> dict[str, object]:
    analysis = _load_confused_analysis(analysis_id)
    # 服务端先复核：以快照为唯一事实来源重算，确认该翻转方案确为可行修订
    new_pattern, check = _recheck_revision(
        analysis, payload.light_index, payload.flip_positions
    )
    proposed = tuple(sorted(payload.flip_positions))
    if not check.get("found"):
        raise HTTPException(
            status_code=409,
            detail="服务端复核未通过：该翻转方案不能使全部灯对在 W 位最小距离均 > 2k，拒绝写入。",
        )
    rev = check["revision"]
    if tuple(rev["flip_positions"]) != proposed or rev["pattern_after"] != new_pattern:
        raise HTTPException(
            status_code=409,
            detail=(
                "服务端复核未通过：该翻转方案不是裁决后的最优可行修订"
                f"（复核应翻转 {rev['flip_positions']}），拒绝写入。"
            ),
        )

    # 复核通过：在 SQLite 事务中核对当前整组灯名、灯串与 W、k 与快照一致后，
    # 仅替换所选灯串；快照删除或状态冲突则拒绝且不写入。
    with db.connect(DB_PATH) as conn:
        ok, conflict, lights = db.apply_revision(
            conn,
            analysis["snapshot"],
            analysis["w"],
            analysis["k"],
            payload.light_index,
            new_pattern,
        )
    if not ok:
        raise HTTPException(status_code=409, detail=conflict)
    return {
        "applied": True,
        "analysis_id": analysis_id,
        "light_index": payload.light_index,
        "light_name": analysis["snapshot"][payload.light_index]["name"],
        "pattern_before": analysis["snapshot"][payload.light_index]["pattern"],
        "pattern_after": new_pattern,
        "flip_positions": list(proposed),
        "global_min_distance": rev["global_min_distance"],
        "lights": lights,
        "note": "仅替换当前工作区该盏灯的灯串；冻结分析与反演记录均未改动，可继续回看。",
    }


# 静态资源（原生 JS 前端）。挂在最后，避免吞掉 /api 路由。
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
