"""Claim Registry + 校验。见 IMPLEMENTATION-P3.md §4.1。

**这是本期唯一真正重要的机制。** 它把 HARKing 从"不该做的事"变成"做不到的事"：

- 正文里每一句强论断都必须带 [claim:<id>] 标记（强论断由 config/claims_patterns.yaml 的模式判定）
- 每个 [claim:id] 的 id 必须在 paper/claims.yaml 注册
- 每条注册项的 source.kind 只有三种：
    contract  — ref 必须是冻结契约里存在的字段
    ablation  — ref 必须是 contract.preregistered_ablations 里的 id
    approved  — audit 表里必须有对应的 claim_approval 卡点记录
- **没有第四种。**「我从结果里看出来的」在 schema 层面不可表达（YAML/DB 都不接受别的 kind）。

任一违规都是 checker 的 FAIL，不是 warning。
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA = REPO_ROOT / "schema" / "claims.sql"

VALID_KINDS = ("contract", "ablation", "approved")
VALID_STATUS = ("pending", "held", "not_held", "inconclusive")

_MARKER_RE = re.compile(r"\[claim:([A-Za-z0-9_\-]+)\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+|\n+")


class ClaimRegistryError(Exception):
    pass


@dataclass(frozen=True)
class ClaimSource:
    kind: str
    ref: str


@dataclass
class Claim:
    id: str
    source: ClaimSource
    evidence: dict = field(default_factory=dict)
    template: str = ""
    status: str = "pending"


@dataclass
class ClaimRegistry:
    claims: List[Claim] = field(default_factory=list)

    def get(self, claim_id: str) -> Optional[Claim]:
        for c in self.claims:
            if c.id == claim_id:
                return c
        return None

    @property
    def ids(self) -> set:
        return {c.id for c in self.claims}


@dataclass
class Violation:
    code: str
    claim_id: Optional[str]
    detail: str


@dataclass
class UnregisteredClaim:
    sentence: str
    line: int
    matched: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------
def make_claim(claim_id: str, kind: str, ref: str, evidence: Optional[dict] = None,
               template: str = "", status: str = "pending") -> Claim:
    """构造一条 claim。kind 不属于三种之一 → 直接拒绝（I17：没有第四种）。"""
    if kind not in VALID_KINDS:
        raise ClaimRegistryError(
            f"非法 source.kind={kind!r}。只允许 {VALID_KINDS} —— "
            f"「从结果里看出来的」这种来源无法表达（I17）。")
    if status not in VALID_STATUS:
        raise ClaimRegistryError(f"非法 status={status!r}")
    return Claim(id=claim_id, source=ClaimSource(kind, ref),
                 evidence=evidence or {}, template=template, status=status)


def load_registry(path: Path) -> ClaimRegistry:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    claims = []
    for raw in data.get("claims", []):
        src = raw.get("source", {}) or {}
        claims.append(make_claim(
            claim_id=raw["id"], kind=src.get("kind", ""), ref=src.get("ref", ""),
            evidence=raw.get("evidence", {}) or {},
            template=(raw.get("template") or "").strip(),
            status=raw.get("status", "pending")))
    return ClaimRegistry(claims)


def load_patterns(path: Optional[Path] = None) -> List[str]:
    import yaml

    path = Path(path) if path else (REPO_ROOT / "config" / "claims_patterns.yaml")
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data.get("strong_claim_patterns", [])


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _contract_field_names(contract) -> set:
    try:
        return set(contract.model_dump().keys())
    except Exception:
        return set(getattr(contract, "__dict__", {}).keys())


def _ablation_ids(contract) -> set:
    return {a.id for a in getattr(contract, "preregistered_ablations", [])}


def load_claim_approvals(audit_db: Path) -> List[dict]:
    """从 audit 表读出所有 claim_approval 的批准记录。"""
    con = sqlite3.connect(audit_db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT gate_type, subject_id, field, decision FROM audit "
            "WHERE gate_type='claim_approval'").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def validate_registry(registry: ClaimRegistry, contract,
                      audit: Optional[Sequence[dict]] = None) -> List[Violation]:
    """校验整个 registry。任一 Violation 都是 checker 的 FAIL。"""
    audit = list(audit or [])
    field_names = _contract_field_names(contract)
    abl_ids = _ablation_ids(contract)
    violations: List[Violation] = []

    for c in registry.claims:
        kind = c.source.kind
        if kind not in VALID_KINDS:
            violations.append(Violation("I17_BAD_KIND", c.id,
                                        f"source.kind={kind!r} 不在 {VALID_KINDS}"))
            continue
        if kind == "contract":
            if c.source.ref not in field_names:
                violations.append(Violation(
                    "I17_CONTRACT_REF", c.id,
                    f"source.ref={c.source.ref!r} 不是契约字段"))
        elif kind == "ablation":
            if c.source.ref not in abl_ids:
                violations.append(Violation(
                    "I17_ABLATION_REF", c.id,
                    f"source.ref={c.source.ref!r} 不在 preregistered_ablations {sorted(abl_ids)}"))
        elif kind == "approved":
            if not _has_approval(audit, c.id):
                violations.append(Violation(
                    "I17_APPROVED_NO_AUDIT", c.id,
                    f"claim {c.id!r} 声称 approved，但 audit 里没有对应的 "
                    f"claim_approval 批准记录"))
    return violations


def _has_approval(audit: Sequence[dict], claim_id: str) -> bool:
    for r in audit:
        if (r.get("gate_type") == "claim_approval"
                and r.get("decision") == "approve"
                and (r.get("subject_id") == claim_id or r.get("field") == claim_id)):
            return True
    return False


# ---------------------------------------------------------------------------
# 正文扫描
# ---------------------------------------------------------------------------
def extract_markers(text: str) -> List[str]:
    return [m.group(1) for m in _MARKER_RE.finditer(text)]


def _is_strong(sentence: str, patterns: Sequence[str]) -> Optional[str]:
    low = sentence.lower()
    for p in patterns:
        if p.lower() in low:
            return p
    return None


def scan_paper_claims(text: str, patterns: Sequence[str]) -> List[UnregisteredClaim]:
    """找出所有匹配强论断模式、但**没有** [claim:id] 标记的句子。

    这些正是 agent 试图偷偷加结论的地方（HARKing）。
    """
    out: List[UnregisteredClaim] = []
    offset = 0
    for sent in _SENTENCE_SPLIT_RE.split(text):
        if not sent.strip():
            continue
        matched = _is_strong(sent, patterns)
        if matched and not _MARKER_RE.search(sent):
            line = text.count("\n", 0, text.find(sent) if sent in text else offset) + 1
            out.append(UnregisteredClaim(sentence=sent.strip(), line=line,
                                         matched=matched))
        offset += len(sent)
    return out


def markers_not_registered(text: str, registry: ClaimRegistry) -> List[Violation]:
    """正文里出现的 [claim:id] 但 id 不在 registry。"""
    ids = registry.ids
    out = []
    for mid in dict.fromkeys(extract_markers(text)):
        if mid not in ids:
            out.append(Violation("I16_MARKER_UNKNOWN", mid,
                                 f"正文引用了未注册的 claim id：{mid!r}"))
    return out


def check_paper(text: str, registry: ClaimRegistry, contract,
                audit: Optional[Sequence[dict]] = None,
                patterns: Optional[Sequence[str]] = None) -> List[Violation]:
    """一站式：registry 合法性 + 正文强论断标记 + 标记 id 已注册。"""
    patterns = patterns if patterns is not None else load_patterns()
    violations: List[Violation] = []
    violations += validate_registry(registry, contract, audit)
    for uc in scan_paper_claims(text, patterns):
        violations.append(Violation(
            "I16_UNREGISTERED", None,
            f"强论断句缺少 [claim:id] 标记（命中模式 {uc.matched!r}）@L{uc.line}：{uc.sentence}"))
    violations += markers_not_registered(text, registry)
    return violations


# ---------------------------------------------------------------------------
# 持久化（可选）
# ---------------------------------------------------------------------------
def init_db(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    con.close()


def persist(registry: ClaimRegistry, contract_id: str, db: Path) -> None:
    """把已校验的 registry 落库。DB 的 CHECK 约束再兜一层 I17。"""
    con = sqlite3.connect(db)
    try:
        for c in registry.claims:
            con.execute(
                "INSERT OR REPLACE INTO claims (claim_id, contract_id, source_kind, "
                "source_ref, evidence, template, status, registered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (c.id, contract_id, c.source.kind, c.source.ref,
                 json.dumps(c.evidence, ensure_ascii=False), c.template, c.status,
                 _now()))
        con.commit()
    finally:
        con.close()


def set_status(db: Path, claim_id: str, status: str) -> None:
    if status not in VALID_STATUS:
        raise ClaimRegistryError(f"非法 status={status!r}")
    con = sqlite3.connect(db)
    try:
        con.execute("UPDATE claims SET status=? WHERE claim_id=?", (status, claim_id))
        con.commit()
    finally:
        con.close()
