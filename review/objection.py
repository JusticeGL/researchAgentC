"""审稿意见 schema + locator 校验。见 IMPLEMENTATION-P4.md §5.1。

**没有合法 locator 的意见直接丢弃**（I26），不是降权，是丢弃：无法定位的意见无法处理，
留着只会稀释人的注意力。

**本 schema 里没有 score 字段。** LLM 审稿人校准很差、更多在给行文流畅度打分 ——
不要存它、不要算它、不要展示它（§5.1）。extra="forbid" 让外部想塞进 score 也会被拒。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

ObjectionKind = Literal["factual", "unsupported", "missing_control", "clarity", "novelty"]

# 合法 locator 形态
_L_RE = re.compile(r"^L(\d+)$", re.IGNORECASE)
_TABLE_RE = re.compile(r"^(?:table|tab\.?|表)\s*(\d+)$", re.IGNORECASE)
_FIG_RE = re.compile(r"^(?:figure|fig\.?|图)\s*(\d+)$", re.IGNORECASE)
_CLAIM_RE = re.compile(r"^claim:([A-Za-z0-9_\-]+)$")
_SECTION_RE = re.compile(r"^§?\s*(\d+(?:\.\d+)*)$")


class Objection(BaseModel):
    """一条审稿意见。locator 必填且必须能在 build/ 里定位，否则丢弃（I26）。

    **注意：没有 score 字段。** extra="forbid" 保证外部无法偷偷加评分。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    locator: str
    kind: ObjectionKind
    checkable: bool
    statement: str
    suggested_check: Optional[str] = None

    @model_validator(mode="after")
    def _check_suggested(self) -> "Objection":
        if self.checkable and not (self.suggested_check and self.suggested_check.strip()):
            raise ValueError("checkable=True 的意见必须给出 suggested_check")
        return self


def _num_lines(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def validate_locator(locator: str, rendered_text: str,
                     registry_ids: Optional[set] = None) -> bool:
    """locator 是否能定位到 build/ 里的一个实际位置。

    支持：L<n> | Table <n> | Fig <n> | claim:<id> | §<n.n>。
    任何无法解析或定位不到的形态 → False（该意见将被丢弃）。
    """
    if not locator or not locator.strip():
        return False
    loc = locator.strip()

    m = _L_RE.match(loc)
    if m:
        n = int(m.group(1))
        return 1 <= n <= _num_lines(rendered_text)

    m = _TABLE_RE.match(loc)
    if m:
        n = m.group(1)
        return bool(re.search(rf"(?:table|表)\s*{n}\b", rendered_text, re.IGNORECASE))

    m = _FIG_RE.match(loc)
    if m:
        n = m.group(1)
        return bool(re.search(rf"(?:figure|fig\.?|图)\s*{n}\b", rendered_text, re.IGNORECASE))

    m = _CLAIM_RE.match(loc)
    if m:
        cid = m.group(1)
        if registry_ids is not None and cid in registry_ids:
            return True
        return f"[claim:{cid}]" in rendered_text

    m = _SECTION_RE.match(loc)
    if m:
        num = m.group(1)
        # 正文里出现 "§n.n"，或某个 markdown 标题以该编号开头
        if f"§{num}" in rendered_text or f"§ {num}" in rendered_text:
            return True
        return bool(re.search(rf"^#{{1,6}}\s+{re.escape(num)}\b", rendered_text, re.MULTILINE))

    return False


def parse_objection(raw: dict) -> Optional[Objection]:
    """把 LLM 产出的原始 dict 解析成 Objection；结构非法（含带了 score）→ None。"""
    try:
        return Objection(**raw)
    except Exception:
        return None
