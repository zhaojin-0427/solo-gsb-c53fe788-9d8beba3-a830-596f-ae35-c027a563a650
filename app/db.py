"""SQLite 持久化层。

四张表
------
- settings：单行（id=1），保存当前窗口上限 W 与误判位数 k。
- lights：当前工作灯串。编辑灯串只影响后续分析，不改既有分析。
- analyses：分析快照。创建分析时把当时的灯串、W、k 与完整结果
  （JSON）整体冻结存入，之后任何编辑都不会影响这些行。
- inversions：观测反演记录。创建时把所选分析快照、观测串、e 与
  反演结果整体冻结存一份副本；即使原分析被删除，反演记录仍可回看。
  编辑当前灯串不影响任何已冻结的分析或反演记录。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_LIGHTS = [
    {"name": "灯 A", "pattern": "11100000"},
    {"name": "灯 B", "pattern": "10100000"},
    {"name": "灯 C", "pattern": "10000001"},
]
DEFAULT_W = 20
DEFAULT_K = 0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    w  INTEGER NOT NULL,
    k  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lights (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    seq     INTEGER NOT NULL,
    name    TEXT NOT NULL,
    pattern TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    note       TEXT NOT NULL DEFAULT '',
    w          INTEGER NOT NULL,
    k          INTEGER NOT NULL,
    light_count INTEGER NOT NULL,
    snapshot   TEXT NOT NULL,
    result     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inversions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    note         TEXT NOT NULL DEFAULT '',
    analysis_id  INTEGER NOT NULL,
    w            INTEGER NOT NULL,
    observation  TEXT NOT NULL,
    e            INTEGER NOT NULL,
    light_count  INTEGER NOT NULL,
    snapshot     TEXT NOT NULL,
    result       TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        row = conn.execute("SELECT id FROM settings WHERE id = 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO settings (id, w, k) VALUES (1, ?, ?)", (DEFAULT_W, DEFAULT_K))
        empty = conn.execute("SELECT COUNT(*) AS c FROM lights").fetchone()["c"]
        if empty == 0:
            for seq, item in enumerate(DEFAULT_LIGHTS):
                conn.execute(
                    "INSERT INTO lights (seq, name, pattern) VALUES (?, ?, ?)",
                    (seq, item["name"], item["pattern"]),
                )


def get_settings(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute("SELECT w, k FROM settings WHERE id = 1").fetchone()
    return {"w": row["w"], "k": row["k"]}


def update_settings(conn: sqlite3.Connection, w: int, k: int) -> dict[str, int]:
    conn.execute("UPDATE settings SET w = ?, k = ? WHERE id = 1", (w, k))
    conn.commit()
    return get_settings(conn)


def list_lights(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, seq, name, pattern FROM lights ORDER BY seq, id").fetchall()
    return [{"id": r["id"], "name": r["name"], "pattern": r["pattern"]} for r in rows]


def replace_lights(conn: sqlite3.Connection, items: list[dict[str, str]]) -> list[dict[str, Any]]:
    """整批替换当前灯串；不触碰 analyses 表中的历史快照。"""
    with conn:  # 事务：任一语句失败整体回滚
        conn.execute("DELETE FROM lights")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'lights'")
        for seq, item in enumerate(items):
            conn.execute(
                "INSERT INTO lights (seq, name, pattern) VALUES (?, ?, ?)",
                (seq, item["name"], item["pattern"]),
            )
    return list_lights(conn)


def create_analysis(
    conn: sqlite3.Connection,
    note: str,
    w: int,
    k: int,
    snapshot: list[dict[str, str]],
    result: dict[str, Any],
) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO analyses (note, w, k, light_count, snapshot, result) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (note, w, k, len(snapshot), json.dumps(snapshot, ensure_ascii=False),
         json.dumps(result, ensure_ascii=False)),
    )
    conn.commit()
    return get_analysis(conn, cur.lastrowid)


def list_analyses(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, created_at, note, w, k, light_count, result "
        "FROM analyses ORDER BY id DESC"
    ).fetchall()
    items = [dict(r) for r in rows]
    for item in items:
        result = json.loads(item.pop("result"))
        item["solvable"] = result.get("solvable")
        item["shortest_window"] = result.get("shortest_window")
    return items


def get_analysis(conn: sqlite3.Connection, analysis_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, created_at, note, w, k, light_count, snapshot, result "
        "FROM analyses WHERE id = ?",
        (analysis_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "note": row["note"],
        "w": row["w"],
        "k": row["k"],
        "light_count": row["light_count"],
        "snapshot": json.loads(row["snapshot"]),
        "result": json.loads(row["result"]),
    }


def delete_analysis(conn: sqlite3.Connection, analysis_id: int) -> bool:
    cur = conn.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
    conn.commit()
    return cur.rowcount > 0


def create_inversion(
    conn: sqlite3.Connection,
    note: str,
    analysis_id: int,
    w: int,
    observation: str,
    e: int,
    snapshot: list[dict[str, str]],
    result: dict[str, Any],
) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO inversions (note, analysis_id, w, observation, e, light_count, snapshot, result) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (note, analysis_id, w, observation, e, len(snapshot),
         json.dumps(snapshot, ensure_ascii=False), json.dumps(result, ensure_ascii=False)),
    )
    conn.commit()
    return get_inversion(conn, cur.lastrowid)


def list_inversions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, created_at, note, analysis_id, w, observation, e, light_count, result "
        "FROM inversions ORDER BY id DESC"
    ).fetchall()
    items = [dict(r) for r in rows]
    for item in items:
        result = json.loads(item.pop("result"))
        item["outcome"] = result.get("outcome")
        item["candidate_count"] = result.get("candidate_count")
        item["undecidable_position"] = result.get("undecidable_position")
    return items


def get_inversion(conn: sqlite3.Connection, inversion_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, created_at, note, analysis_id, w, observation, e, light_count, snapshot, result "
        "FROM inversions WHERE id = ?",
        (inversion_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "note": row["note"],
        "analysis_id": row["analysis_id"],
        "w": row["w"],
        "observation": row["observation"],
        "e": row["e"],
        "light_count": row["light_count"],
        "snapshot": json.loads(row["snapshot"]),
        "result": json.loads(row["result"]),
    }


def delete_inversion(conn: sqlite3.Connection, inversion_id: int) -> bool:
    cur = conn.execute("DELETE FROM inversions WHERE id = ?", (inversion_id,))
    conn.commit()
    return cur.rowcount > 0
