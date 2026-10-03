"""航标灯质可辨性本地 Web 预检台 —— FastAPI 入口。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .engine import analyze, invert
from .schemas import AnalysisCreate, InversionCreate, LightsPayload, SettingsPayload

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


# 静态资源（原生 JS 前端）。挂在最后，避免吞掉 /api 路由。
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
