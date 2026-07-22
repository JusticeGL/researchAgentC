"""适配层测试。见 IMPLEMENTATION-P2.md §4.8、Phase 9。"""
import pytest


def test_aide_availability_probe():
    from adapters import aide_adapter

    # 未安装 aideml 时应为 False；安装后为 True。无论如何不抛。
    assert isinstance(aide_adapter.is_available(), bool)


def test_build_proposer_requires_aide():
    from adapters import aide_adapter

    if not aide_adapter.is_available():
        # 未装全依赖：无 agent 也应抛 AideUnavailable
        with pytest.raises(aide_adapter.AideUnavailable):
            aide_adapter.build_proposer()
    else:
        # 已装：给了 None agent 仍应拒绝（需要真实 Agent）
        with pytest.raises(aide_adapter.AideUnavailable):
            aide_adapter.build_proposer(agent=None)


def test_has_llm_key_is_bool():
    from adapters import aide_adapter

    assert isinstance(aide_adapter.has_llm_key(), bool)


def test_run_node_sh_exists():
    from adapters import aide_adapter

    p = aide_adapter.run_node_sh()
    assert p.exists()
    assert "--network=none" in p.read_text(encoding="utf-8")


def test_evaluator_rejects_test_phase(db_path, base_run_fields):
    from adapters import evaluator
    from core import results

    rid = results.record_run(db=db_path, metrics={"acc": 0.8},
                             **base_run_fields(phase="test"))
    with pytest.raises(ValueError):
        evaluator.dev_score_from_run(rid, "acc", db_path)


def test_evaluator_reads_dev_score(db_path, base_run_fields):
    from adapters import evaluator
    from core import results

    rid = results.record_run(db=db_path, metrics={"acc": 0.83},
                             **base_run_fields(phase="dev"))
    assert evaluator.dev_score_from_run(rid, "acc", db_path) == pytest.approx(0.83)


def test_pick_primary_run():
    from adapters import evaluator

    assert evaluator.pick_primary_run("r1", None) == "r1"
    d = {"csp_lda": "r_a", "null": "r_b"}
    assert evaluator.pick_primary_run(d, "csp_lda") == "r_a"
    # 无指定 → 按 key 排序取第一个（确定性）
    assert evaluator.pick_primary_run(d, None) == "r_a"
