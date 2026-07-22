"""模板体系 + hypothesis_held 推导测试。见 IMPLEMENTATION-P3.md §4.2、§6 Phase 12。"""
import pytest


def test_hypothesis_held_matches_confirm_terminal_state():
    """逐条核对 §4.2 的终态→(held, 模板) 表。"""
    from loop.confirm import ConfirmState
    from writing import templates

    # DONE + 达阈值 → True / positive
    d = templates.derive_decision(ConfirmState.DONE, test_passed=True)
    assert d.hypothesis_held is True and d.template == "positive"

    # DONE + 未达阈值 → False / negative（dev 确认但 test 没成立）
    d = templates.derive_decision(ConfirmState.DONE, test_passed=False)
    assert d.hypothesis_held is False and d.template == "negative"

    # 搜索结束、人工选择直接写负面 → False / negative
    d = templates.derive_decision(ConfirmState.REJECTED,
                                  wrote_negative_after_search=True)
    assert d.hypothesis_held is False and d.template == "negative"

    # 预算耗尽且未确认 → None / inconclusive
    d = templates.derive_decision(ConfirmState.BUDGET_EXHAUSTED)
    assert d.hypothesis_held is None and d.template == "inconclusive"


def test_done_requires_test_result():
    from loop.confirm import ConfirmState
    from writing import templates

    with pytest.raises(ValueError):
        templates.derive_decision(ConfirmState.DONE)  # 缺 test_passed


def test_abstract_first_sentence_deterministic(sample_contract):
    from writing import templates

    pos = templates.abstract_first_sentence(sample_contract, True)
    neg = templates.abstract_first_sentence(sample_contract, False)
    inc = templates.abstract_first_sentence(sample_contract, None)
    assert pos != neg != inc
    assert sample_contract.hypothesis in pos
    assert "负面" in neg
    # 确定性：同输入同输出
    assert templates.abstract_first_sentence(sample_contract, False) == neg


def test_templates_exist_and_have_required_sections():
    from writing import templates

    for name in ("positive", "negative", "inconclusive"):
        txt = templates.load_template(name)
        assert "## Abstract" in txt
        assert "<<ABSTRACT_FIRST_SENTENCE>>" in txt
        assert "## Limitations" in txt

    neg = templates.load_template("negative")
    # negative 骨架的四项必填素材
    assert "假设" in neg
    assert "NOVELTY_NOTE" in neg          # 为何当时合理
    assert "被排除的方向" in neg           # deadend
    assert "DEV_TEST_GAP" in neg          # dev/test gap
    assert "预算" in neg                   # 实验数与预算
