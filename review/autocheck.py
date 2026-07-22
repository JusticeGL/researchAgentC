"""checkable 意见转断言。见 IMPLEMENTATION-P4.md §5.2。

**这是本模块唯一真正有价值的部分。** 把一部分审稿意见变成自动化验证，
人只需要看剩下的主观部分 —— 而那部分本来就该人看。

对每条 checkable=True 的意见，把 suggested_check 转成一个断言并跑：
  通过 → 意见不成立，驳回并记录（rejected）
  不通过 → 意见成立，进人工清单最高优先级（upheld）
  转不成断言的 → 降级为 checkable=False（not_convertible）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

REJECTED = "rejected"           # 意见不成立（断言通过）
UPHELD = "upheld"               # 意见成立（断言未通过）→ 最高优先级
NOT_CONVERTIBLE = "not_convertible"  # 转不成断言 → 降级为 checkable=False


@dataclass
class AutocheckContext:
    provenance: Optional[dict] = None      # build/provenance.json
    corpus_db: Optional[Path] = None
    registry: Optional[object] = None      # writing.claims.ClaimRegistry
    results_path: Optional[Path] = None


@dataclass
class Verdict:
    objection: object
    status: str                            # rejected|upheld|not_convertible
    detail: str = ""


# 每个内置断言：命中则返回 (status, detail)；不适用返回 None，交给下一个。
CheckFn = Callable[[object, AutocheckContext], Optional[tuple]]

_VARIANCE_KW = ("方差", "variance", "std", "标准差", "error bar", "误差")
_EVIDENCE_KW = ("证据", "evidence", "support", "对应")


def _check_variance(obj, ctx: AutocheckContext) -> Optional[tuple]:
    """"没有报告方差" → 查 provenance 里是否有 agg 替换（带 std / n≥2）。"""
    text = f"{obj.statement} {obj.suggested_check or ''}".lower()
    if not any(kw in text for kw in _VARIANCE_KW):
        return None
    if not ctx.provenance:
        return (NOT_CONVERTIBLE, "无 provenance，无法核查方差")
    reps = ctx.provenance.get("replacements", [])
    reported = any(r.get("kind") == "agg" and (r.get("n") or 0) >= 2 for r in reps)
    if reported:
        return (REJECTED, "provenance 里存在 n≥2 的聚合替换，方差已报告，意见不成立")
    return (UPHELD, "provenance 里没有任何聚合（agg）替换，确实未报告方差")


def _check_claim_evidence(obj, ctx: AutocheckContext) -> Optional[tuple]:
    """"claim X 没有对应证据" → 查 claim registry：该 claim 是否已注册且有合法来源。"""
    loc = obj.locator
    text = f"{obj.statement} {obj.suggested_check or ''}".lower()
    if not loc.startswith("claim:") and not any(kw in text for kw in _EVIDENCE_KW):
        return None
    if ctx.registry is None:
        return (NOT_CONVERTIBLE, "无 claim registry，无法核查证据")
    cid = loc.split(":", 1)[1] if loc.startswith("claim:") else None
    if not cid:
        return (NOT_CONVERTIBLE, "意见未指向具体 claim id")
    claim = ctx.registry.get(cid)
    if claim is not None and getattr(claim.source, "kind", None):
        return (REJECTED,
                f"claim {cid} 已注册，来源 kind={claim.source.kind}，有据可依，意见不成立")
    return (UPHELD, f"claim {cid} 未在 registry 注册或无合法来源，意见成立")


_BUILTIN_CHECKS: List[CheckFn] = [_check_variance, _check_claim_evidence]


def autocheck(objections: List[object], ctx: Optional[AutocheckContext] = None,
              extra_checks: Optional[List[CheckFn]] = None) -> List[Verdict]:
    """对每条 checkable 意见跑断言。非 checkable 的原样返回 not_convertible（无需处理）。"""
    ctx = ctx or AutocheckContext()
    checks = list(extra_checks or []) + _BUILTIN_CHECKS
    out: List[Verdict] = []
    for obj in objections:
        if not getattr(obj, "checkable", False):
            out.append(Verdict(obj, NOT_CONVERTIBLE, "非 checkable，留给人工"))
            continue
        result = None
        for fn in checks:
            result = fn(obj, ctx)
            if result is not None:
                break
        if result is None:
            out.append(Verdict(obj, NOT_CONVERTIBLE,
                               "无法把 suggested_check 转成断言，降级为主观意见"))
        else:
            status, detail = result
            out.append(Verdict(obj, status, detail))
    return out
