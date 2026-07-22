"""成本预估测试。见 IMPLEMENTATION-P2.md §4.9。"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOP_CFG = REPO_ROOT / "config" / "loop.yaml"


def _cfg():
    from loop import run_loop

    return run_loop.load_config(LOOP_CFG)


def test_estimate_three_bands_monotonic(sample_contract):
    from loop import cost

    est = cost.estimate(sample_contract, _cfg(), single_run_minutes=5.0)
    assert est.optimistic.usd <= est.median.usd <= est.pessimistic.usd
    assert est.optimistic.gpu_hours <= est.median.gpu_hours <= est.pessimistic.gpu_hours


def test_estimate_exceeds_flags_over_budget(sample_contract):
    from loop import cost

    # 用离谱的单次时长把悲观档顶穿预算
    est = cost.estimate(sample_contract, _cfg(), single_run_minutes=10000.0)
    assert est.exceeds(sample_contract.budget) is True


def test_median_single_run_minutes_fallback(tmp_path, sample_contract):
    from core import results
    from loop import cost

    p = tmp_path / "results.sqlite"
    results.init_db(p)
    # 无 run → 回退 default
    assert cost.median_single_run_minutes(p, sample_contract.contract_id,
                                          default=7.0) == 7.0
