"""负面结果路径。见 IMPLEMENTATION-P3.md §4.2、§5。

系统必须能自然地写出"假设未成立"的论文，而不是卡死或粉饰（I18）。
最有价值的情形是：dev 上确认了、test 上没成立 —— 说明搜索过程过拟合。

本模块只做确定性的素材汇总（实验数/预算/dev-test gap/被排除方向），
再交给 writing.compose 用 negative 模板渲染。**不做任何"把负面说成正面"的加工。**
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional


def budget_used_str(contract, results_path: Path) -> str:
    from core import results

    runs = results.query_runs(db=results_path, contract_id=contract.contract_id)
    gpu = sum(r.gpu_hours or 0 for r in runs)
    usd = sum(r.cost_usd or 0 for r in runs)
    wall = sum((r.wall_clock_s or 0) for r in runs) / 3600.0
    return f"GPU {gpu:.2f}h / 花费 ${usd:.2f} / 墙钟 {wall:.2f}h（共 {len(runs)} 次 run）"


def dev_test_gap_str(dev_score: Optional[float], test_score: Optional[float],
                     threshold: float, direction: str) -> str:
    """量化 dev/test gap。dev 过线但 test 未过 → 明确指出这是搜索过拟合的证据。"""
    if dev_score is None or test_score is None:
        return "dev/test gap 不适用（测试集未开启或无 dev 分数）。"
    gap = dev_score - test_score
    dev_ok = (dev_score >= threshold) if direction == "maximize" else (dev_score <= threshold)
    test_ok = (test_score >= threshold) if direction == "maximize" else (test_score <= threshold)
    base = (f"dev 分数约 {dev_score:.3f}，test 分数约 {test_score:.3f}，"
            f"gap≈{gap:.3f}（阈值 {threshold}）。")
    if dev_ok and not test_ok:
        base += "dev 过线而 test 未过 —— 这是搜索过程存在过拟合的直接证据。"
    return base


def build_negative_stats(contract, results_path: Path, *, n_nodes: Optional[int] = None,
                         dev_score: Optional[float] = None,
                         test_score: Optional[float] = None,
                         title: Optional[str] = None) -> dict:
    return {
        "title": title or f"负面结果：{contract.question[:60]}",
        "n_nodes": n_nodes if n_nodes is not None else "N/A",
        "budget_used": budget_used_str(contract, results_path),
        "dev_test_gap": dev_test_gap_str(dev_score, test_score,
                                         contract.success_threshold, contract.direction),
    }


def compose_negative_paper(contract, results_path: Path, ledger_db: Optional[Path],
                           *, contract_hash: str, out_path: Path,
                           n_nodes: Optional[int] = None,
                           dev_score: Optional[float] = None,
                           test_score: Optional[float] = None,
                           terminal_state: str = "DONE",
                           test_passed: Optional[bool] = False) -> dict:
    """一站式：汇总素材 → negative 模板 → 写出 paper markdown + meta。"""
    from loop import ledger
    from loop.confirm import ConfirmState
    from writing import compose, templates

    if terminal_state == "DONE":
        decision = templates.derive_decision(ConfirmState.DONE, test_passed=test_passed)
    elif terminal_state == "BUDGET_EXHAUSTED":
        decision = templates.derive_decision(ConfirmState.BUDGET_EXHAUSTED)
    else:
        decision = templates.derive_decision(ConfirmState.REJECTED,
                                             wrote_negative_after_search=True)

    lessons = []
    if ledger_db is not None:
        lessons = ledger.active_lessons(ledger_db, contract.contract_id)

    stats = build_negative_stats(contract, results_path, n_nodes=n_nodes,
                                 dev_score=dev_score, test_score=test_score)
    return compose.compose_paper(contract, decision, contract_hash=contract_hash,
                                 stats=stats, lessons=lessons, out_path=out_path,
                                 confirm_terminal_state=terminal_state)
