"""冻结实验台测试。见 IMPLEMENTATION.md §5.3、§6。

MOABB/MNE 缺失时，需要真数据的测试用 importorskip 自动 skip；
harness_hash / budget / null-pipeline（合成数据）等离线测试始终运行。
"""
import numpy as np
import pytest


# ---------------------------------------------------------------------------
# harness_hash（I6）
# ---------------------------------------------------------------------------
def test_harness_hash_is_hex_and_stable():
    import harness

    h1 = harness.harness_hash()
    h2 = harness.harness_hash()
    assert h1 == h2
    assert len(h1) == 64
    int(h1, 16)  # 是合法 hex


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------
def test_budget_records_wall_clock():
    from harness.budget import budget

    class C:
        class budget:  # noqa: N801
            per_node_gpu_hours = 100.0

    with budget(C, node_id="n1") as usage:
        sum(range(1000))
    assert usage.wall_clock_s >= 0
    assert usage.gpu_hours == 0.0  # 无 GPU


def test_budget_exceeded_raises():
    from harness.budget import BudgetExceeded, budget

    class C:
        class budget:  # noqa: N801
            per_node_gpu_hours = 0.0  # 任何 GPU 用量都超

    with pytest.raises(BudgetExceeded):
        with budget(C, node_id="n1", has_gpu=True):
            # has_gpu=True 会把哪怕极短的耗时折算成 >0 的 gpu_hours
            for _ in range(10000):
                pass


# ---------------------------------------------------------------------------
# null pipeline 在 chance 附近（离线合成数据版本）
# ---------------------------------------------------------------------------
def test_null_pipeline_scores_at_chance_synthetic():
    """核心思想（§6）：只输出常数标签的 pipeline，准确率应在 1/n_classes 附近。
    这里用 4 类均衡合成数据（对应 BNCI2014_001 的 chance=0.25），
    用真实数据时 chance 从 paradigm/dataset 读取，不写死 0.5。
    """
    from sklearn.model_selection import cross_val_score

    from harness.evaluate import make_null_pipeline

    rng = np.random.RandomState(0)
    n_classes = 4
    n_per = 60
    # 3D 类 epochs 数据 (trials, channels, times)，标签与特征无关联 → 只能到 chance
    X = rng.randn(n_classes * n_per, 8, 50)
    y = np.repeat(np.arange(n_classes), n_per)
    pipe = make_null_pipeline()
    scores = cross_val_score(pipe, X, y, cv=5)
    chance = 1.0 / n_classes
    assert abs(scores.mean() - chance) < 0.08, (
        f"null pipeline 均值 {scores.mean():.3f} 偏离 chance {chance} 太多 —— "
        f"可能评测泄漏了标签"
    )


# ---------------------------------------------------------------------------
# 需要真 MOABB 的路径（离线自动 skip）
# ---------------------------------------------------------------------------
def test_moabb_dataset_resolves():
    from tests._helpers import require_moabb

    require_moabb()
    from harness import data as hdata

    ds = hdata.get_dataset("BNCI2014_001")
    assert ds is not None
    pdm = hdata.get_paradigm("motor_imagery")
    assert pdm is not None
    assert hdata.n_classes(ds, pdm) >= 2


def test_evaluate_returns_run_id_not_float(tmp_path):
    from tests._helpers import require_moabb

    require_moabb()
    from core import results
    from core.contract import (AblationSpec, BaselineSpec, Budget, Contract,
                               StatPlan)
    from harness.evaluate import evaluate_dev, make_null_pipeline

    db = tmp_path / "results.sqlite"
    results.init_db(db)
    contract = Contract(
        contract_id="c_h", version=1, parent_version=None,
        question="q", hypothesis="h", datasets=["BNCI2014_001"],
        split_protocol="within_session", paradigm="motor_imagery",
        baselines=[BaselineSpec(name="null", cite_key="x", reproduced_run_ids=[])],
        primary_metric="acc", success_threshold=0.75, direction="maximize",
        stat_plan=StatPlan(n_seeds=1, test="paired_t", correction="none", min_effect_size=0.0),
        budget=Budget(gpu_hours=1, usd=1, wall_clock_h=1, per_node_gpu_hours=1),
        kill_criteria=["x"],
        preregistered_ablations=[AblationSpec(id="a1", description="d", falsifies="f")],
        novelty_evidence=["x"], novelty_note="n",
    )
    try:
        out = evaluate_dev({"null": make_null_pipeline()}, contract, seed=0,
                           results_path=db)
    except AssertionError:
        raise
    except Exception as e:  # 数据集需联网下载 / ~/mne_data 不存在
        pytest.skip(f"MOABB 数据集不可用（需联网下载）：{type(e).__name__}: {e}")
    assert isinstance(out["null"], str)  # 返回 run_id，不是 float
    m = results.get_metric(out["null"], "acc", db=db)
    assert isinstance(m, results.Metric)
