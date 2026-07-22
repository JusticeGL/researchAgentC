"""模板选择 + hypothesis_held 的确定性推导。见 IMPLEMENTATION-P3.md §4.2。

hypothesis_held **不由任何 LLM 判断**，只由确认协议的终态 + 是否达阈值确定：

| 终态                         | test 结果      | hypothesis_held | 模板         |
| DONE                        | 达到 threshold | True            | positive     |
| DONE                        | 未达 threshold | False           | negative     |
| 搜索结束→人工选择直接写负面    | 未开测试集      | False           | negative     |
| BUDGET_EXHAUSTED 且未确认     | 未开测试集      | None            | inconclusive |

第二行（dev 上确认了、test 上没成立）是最有价值的负面结果，说明搜索过程过拟合。
系统必须能自然地写出这篇论文。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "paper" / "templates"


@dataclass(frozen=True)
class TerminalDecision:
    hypothesis_held: Optional[bool]     # True | False | None
    template: str                       # positive | negative | inconclusive


def derive_decision(terminal_state, test_passed: Optional[bool] = None,
                    wrote_negative_after_search: bool = False) -> TerminalDecision:
    """从确认协议终态推导 (hypothesis_held, template)。

    terminal_state: loop.confirm.ConfirmState
    test_passed:    仅当 terminal_state==DONE 时有意义（测试集是否达阈值）
    """
    from loop.confirm import ConfirmState

    if terminal_state == ConfirmState.DONE:
        if test_passed is None:
            raise ValueError("DONE 终态必须提供 test_passed（测试集是否达阈值）")
        return (TerminalDecision(True, "positive") if test_passed
                else TerminalDecision(False, "negative"))

    if terminal_state == ConfirmState.BUDGET_EXHAUSTED:
        # 预算耗尽且从未完成确认 / 从未开测试集 → 无结论
        return TerminalDecision(None, "inconclusive")

    # 搜索结束但未开测试集：若人工选择直接写负面 → negative；否则无结论
    if wrote_negative_after_search:
        return TerminalDecision(False, "negative")
    return TerminalDecision(None, "inconclusive")


def abstract_first_sentence(contract, hypothesis_held: Optional[bool]) -> str:
    """Abstract 首句由模板 + hypothesis_held 确定性渲染，**不经 LLM**（§2）。"""
    h = contract.hypothesis
    if hypothesis_held is True:
        return f"本文在预注册协议下验证了假设「{h}」，并在一次性测试集上达到了预注册阈值。"
    if hypothesis_held is False:
        return f"本文报告一个负面结果：预注册假设「{h}」未成立。"
    return f"本文为一份无结论报告：在预算耗尽前未能完成对假设「{h}」的确认。"


def load_template(name: str) -> str:
    if name not in ("positive", "negative", "inconclusive"):
        raise ValueError(f"未知模板：{name}")
    return (TEMPLATE_DIR / f"{name}.md").read_text(encoding="utf-8")
