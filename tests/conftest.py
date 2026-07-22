import sys
from pathlib import Path

import pytest

# 让 core / harness 可被 import（仓库根加入 sys.path）
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def db_path(tmp_path):
    """一个初始化好的空结果库，返回其路径。"""
    from core import results

    p = tmp_path / "results.sqlite"
    results.init_db(p)
    return p


def _base_run_fields(**overrides):
    """一组合法的 record_run 字段，测试里按需覆盖。"""
    fields = dict(
        contract_id="c_test",
        contract_hash="deadbeef",
        harness_hash="cafef00d",
        code_sha="0" * 40,
        config_hash="cfg123",
        data_sha="data123",
        env_hash="env123",
        seed=0,
        split="within_session",
        phase="dev",
        status="ok",
    )
    fields.update(overrides)
    return fields


@pytest.fixture
def base_run_fields():
    return _base_run_fields


def _make_contract(**overrides):
    """一份合法契约，P2 的树/上下文/确认测试复用。"""
    from core.contract import (AblationSpec, BaselineSpec, Budget, Contract,
                               StatPlan)

    fields = dict(
        contract_id="c_test", version=1, parent_version=None,
        question="CSP+LDA 能否在 BNCI2014_001 上达到 0.75 被试内准确率？",
        hypothesis="空间滤波捕捉的判别性节律是分类性能主要来源。",
        datasets=["BNCI2014_001"], split_protocol="within_session",
        paradigm="motor_imagery",
        baselines=[BaselineSpec(name="csp_lda", cite_key="ang2012csp",
                                reproduced_run_ids=[])],
        primary_metric="acc", success_threshold=0.75, direction="maximize",
        stat_plan=StatPlan(n_seeds=5, test="paired_t", correction="none",
                           min_effect_size=0.02),
        budget=Budget(gpu_hours=10, usd=20, wall_clock_h=24, per_node_gpu_hours=2),
        kill_criteria=["dev acc 连续 10 节点低于 chance+0.05"],
        preregistered_ablations=[
            AblationSpec(id="a1", description="移除 CSP 只留原始通道",
                         falsifies="性能来自空间滤波而非分类器")],
        novelty_evidence=["ang2012csp"],
        novelty_note="被试内协议下重新评估经典 baseline。",
    )
    fields.update(overrides)
    return Contract(**fields)


@pytest.fixture
def sample_contract():
    return _make_contract()
