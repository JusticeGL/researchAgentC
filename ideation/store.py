"""ideas / drafts / axis_scores 持久化。见 schema/ideas.sql。

I22 的落库面：incomplete 的 draft 也会入 idea_drafts 表（保留、可复查），
但 rankable()（fill.py）会把它挡在排名之外。axis_scores 结构上无法写 'overall'（I27）。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "ideas.sql"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    con.close()


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def add_idea(db: Path, idea) -> str:
    con = _connect(db)
    try:
        con.execute(
            "INSERT OR REPLACE INTO ideas (idea_id, model, seed, text, query_hint, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (idea.idea_id, idea.model, idea.seed, idea.text,
             getattr(idea, "query_hint", None), _now()))
        con.commit()
    finally:
        con.close()
    return idea.idea_id


def add_draft(db: Path, draft) -> str:
    """把 ContractDraft 落库（含 incomplete）。budget 等非 JSON 对象转成可序列化。"""
    payload = asdict(draft) if is_dataclass(draft) else dict(draft)
    budget = payload.get("budget")
    if budget is not None and not isinstance(budget, (dict, str, int, float)):
        payload["budget"] = getattr(budget, "model_dump", lambda: str(budget))()
    con = _connect(db)
    try:
        con.execute(
            "INSERT OR REPLACE INTO idea_drafts (idea_id, status, missing_fields, draft, "
            "novelty_verdict, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (draft.idea_id, draft.status,
             json.dumps(draft.missing_fields, ensure_ascii=False),
             json.dumps(payload, ensure_ascii=False, default=str),
             payload.get("novelty_verdict"), _now()))
        con.commit()
    finally:
        con.close()
    return draft.idea_id


def add_axis_scores(db: Path, idea_id: str, axis_scores) -> None:
    """写五轴。axis 只能是五个之一（DB CHECK 兜底 I27）。"""
    con = _connect(db)
    try:
        for row in axis_scores.as_rows():
            con.execute(
                "INSERT OR REPLACE INTO axis_scores (idea_id, axis, value, label, "
                "rationale, evidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (idea_id, row.axis, row.value, row.label, row.rationale,
                 json.dumps(row.evidence, ensure_ascii=False), _now()))
        con.commit()
    finally:
        con.close()


def query_ideas(db: Path) -> List[sqlite3.Row]:
    con = _connect(db)
    try:
        return con.execute("SELECT * FROM ideas ORDER BY created_at").fetchall()
    finally:
        con.close()


def query_drafts(db: Path, status: Optional[str] = None) -> List[sqlite3.Row]:
    con = _connect(db)
    try:
        if status:
            return con.execute("SELECT * FROM idea_drafts WHERE status=?",
                               (status,)).fetchall()
        return con.execute("SELECT * FROM idea_drafts").fetchall()
    finally:
        con.close()


def get_idea(db: Path, idea_id: str) -> Optional[sqlite3.Row]:
    con = _connect(db)
    try:
        return con.execute("SELECT * FROM ideas WHERE idea_id=?", (idea_id,)).fetchone()
    finally:
        con.close()
