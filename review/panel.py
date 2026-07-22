"""多模型审稿编排。见 IMPLEMENTATION-P4.md §5.3。

3 个模型，各审一遍，**互不相看**。输出合并成一张按 (checkable, kind) 分组的清单，
去重（同一 locator + 同一 kind 视为重复），进 review_comments 卡点。

无合法 locator 的意见在此丢弃（I26）；表里也没有 score 字段（§5.1）。
review_fn 以依赖注入传入（离线可测；live 时注入真实模型后端）。
"""
from __future__ import annotations

import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from review import autocheck as ac
from review.objection import Objection, parse_objection, validate_locator

# review_fn(model, rendered_text) -> [原始意见 dict, ...]
ReviewFn = Callable[[str, str], List[dict]]

_SCHEMA = Path(__file__).resolve().parent.parent / "schema" / "review.sql"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    con.close()


def collect(rendered_text: str, models: List[str], review_fn: ReviewFn,
            registry_ids: Optional[set] = None
            ) -> Tuple[List[Tuple[str, Objection]], List[dict]]:
    """每个模型独立审一遍。返回 (保留的 (model, Objection) 列表, 被丢弃的原始意见列表)。

    丢弃条件：解析失败（含带 score 等非法字段）或 locator 无法定位（I26）。
    """
    kept: List[Tuple[str, Objection]] = []
    discarded: List[dict] = []
    for model in models:
        # 每个模型只拿到论文文本，看不到别的模型的意见 —— 互不相看。
        for raw in review_fn(model, rendered_text):
            obj = parse_objection(raw)
            if obj is None:
                discarded.append({"model": model, "raw": raw, "reason": "解析失败/含非法字段"})
                continue
            if not validate_locator(obj.locator, rendered_text, registry_ids):
                discarded.append({"model": model, "raw": raw,
                                  "reason": f"locator {obj.locator!r} 无法定位，丢弃（I26）"})
                continue
            kept.append((model, obj))
    return kept, discarded


def _dedup(kept: List[Tuple[str, Objection]]) -> List[Tuple[str, Objection]]:
    """同一 (locator, kind) 视为重复，保留首个。"""
    seen: set = set()
    out: List[Tuple[str, Objection]] = []
    for model, obj in kept:
        key = (obj.locator, obj.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append((model, obj))
    return out


def panel(paper_id: str, rendered_text: str, models: List[str], review_fn: ReviewFn,
          registry_ids: Optional[set] = None,
          ctx: Optional[ac.AutocheckContext] = None,
          db: Optional[Path] = None) -> dict:
    """完整编排：收集 → 丢弃无 locator → 去重 → autocheck → 按 (checkable,kind) 分组。

    返回 {"comments": [...], "discarded": [...], "grouped": {...}}。
    db 非空时把保留的意见写入 review_comments（无 score 字段）。
    """
    kept, discarded = collect(rendered_text, models, review_fn, registry_ids)
    kept = _dedup(kept)

    objs = [obj for _model, obj in kept]
    verdicts = ac.autocheck(objs, ctx=ctx)
    verdict_by_id = {id(v.objection): v for v in verdicts}

    comments: List[dict] = []
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for model, obj in kept:
        v = verdict_by_id.get(id(obj))
        row = {
            "locator": obj.locator, "kind": obj.kind, "checkable": obj.checkable,
            "statement": obj.statement, "suggested_check": obj.suggested_check,
            "model": model,
            "autocheck_result": (v.status if v else None),
            "autocheck_detail": (v.detail if v else ""),
        }
        comments.append(row)
        grouped[f"{'checkable' if obj.checkable else 'subjective'}:{obj.kind}"].append(row)

    if db is not None:
        _persist(paper_id, comments, db)

    return {"comments": comments, "discarded": discarded, "grouped": dict(grouped)}


def _persist(paper_id: str, comments: List[dict], db: Path) -> None:
    con = sqlite3.connect(db)
    try:
        for r in comments:
            con.execute(
                "INSERT INTO review_comments (comment_id, paper_id, locator, kind, "
                "checkable, statement, suggested_check, autocheck_result, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"rc_{uuid.uuid4().hex[:8]}", paper_id, r["locator"], r["kind"],
                 int(r["checkable"]), r["statement"], r["suggested_check"],
                 r["autocheck_result"], r["model"], _now()))
        con.commit()
    finally:
        con.close()
