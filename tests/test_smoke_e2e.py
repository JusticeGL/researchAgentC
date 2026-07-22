"""端到端 smoke。见 IMPLEMENTATION.md §7 Phase 5。

从空库开始 → 建契约 → 跑 3 个 seed 的 baseline → 检索 3 篇文献 →
渲染一段带 {{agg:...}} 和 \\cite{...} 的 markdown → make check 全绿。

离线环境无法真跑 MOABB / OpenAlex，故本 e2e：
  - baseline 用 record_run 直接写入（带正确的 contract_hash / harness_hash）
  - 文献用 corpus.add_paper 直接入库（带 DOI）
两者替代 harness.evaluate_dev / corpus.search 的联网部分，
但走的是完全相同的 results / corpus / render / checker 路径，因此仍是真实的不变式验收。
`make check` 全绿（无 FAIL）即通过。
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_CFG = REPO_ROOT / "config" / "render.yaml"


def _build_contract():
    from core.contract import (AblationSpec, BaselineSpec, Budget, Contract,
                               StatPlan)

    return Contract(
        contract_id="c_smoke", version=1, parent_version=None,
        question="CSP+LDA 能否在 BNCI2014_001 运动想象上达到 0.75 的被试内准确率？",
        hypothesis="空间滤波捕捉的判别性感觉运动节律是分类性能的主要来源。",
        datasets=["BNCI2014_001"], split_protocol="within_session",
        paradigm="motor_imagery",
        baselines=[BaselineSpec(name="csp_lda", cite_key="ang2012csp",
                                reproduced_run_ids=[])],
        primary_metric="acc", success_threshold=0.75, direction="maximize",
        stat_plan=StatPlan(n_seeds=3, test="paired_t", correction="none",
                           min_effect_size=0.02),
        budget=Budget(gpu_hours=10, usd=20, wall_clock_h=24, per_node_gpu_hours=2),
        kill_criteria=["dev acc 连续 10 节点低于 chance+0.05"],
        preregistered_ablations=[
            AblationSpec(id="a1", description="移除 CSP 只留原始通道",
                         falsifies="性能来自空间滤波而非分类器")
        ],
        novelty_evidence=["ang2012csp"],
        novelty_note="相较最近邻工作，本契约在被试内协议下重新评估经典 baseline。",
    )


def test_smoke_reproduce(tmp_path):
    import harness
    from core import checker, corpus, gates, render, results

    # 目录
    data = tmp_path / "data"
    data.mkdir()
    results_db = data / "results.sqlite"
    corpus_db = data / "corpus.sqlite"
    audit_db = data / "audit.sqlite"
    contracts_dir = tmp_path / "contracts"
    build_dir = tmp_path / "build"

    results.init_db(results_db)
    corpus.init_db(corpus_db)
    gates.init_db(audit_db)

    # 1. 契约
    contract = _build_contract()

    # 2. 跑 3 个 seed 的 baseline（离线：直接 record_run，走真实结果库路径）
    contract_hash = contract.content_hash_value()
    harness_hash = harness.harness_hash()
    run_ids = []
    for seed, acc in zip(range(3), [0.78, 0.81, 0.80]):
        rid = results.record_run(
            db=results_db, metrics={"acc": acc},
            contract_id=contract.contract_id, contract_hash=contract_hash,
            harness_hash=harness_hash, code_sha="0" * 40, config_hash="csp_lda",
            data_sha="bnci2014001-fp", env_hash="env", seed=seed,
            split="within_session", phase="dev", status="ok",
        )
        run_ids.append(rid)

    # 回填 reproduced_run_ids（不改变 hash）
    contract.baselines[0].reproduced_run_ids = list(run_ids)
    assert contract.content_hash_value() == contract_hash

    # 3. 人工卡点：逐字段过契约（自动全 approve）+ 冻结
    assert gates.review_contract(contract, db=audit_db) is True
    frozen = contract.freeze(out_dir=contracts_dir, write=True)
    contract_path = contracts_dir / f"{frozen.contract_id}.v{frozen.version}.json"
    assert contract_path.exists()

    # 4. 检索 3 篇文献（离线：直接入库，带 DOI）
    corpus.add_paper(corpus_db, key="ang2012csp",
                     title="Filter Bank Common Spatial Pattern (FBCSP)",
                     authors="Ang, K. K.", year=2012, doi="10.3389/fnins.2012.00039",
                     oa_status="open")
    corpus.add_paper(corpus_db, key="lawhern2018eegnet", title="EEGNet",
                     authors="Lawhern, V.", year=2018,
                     doi="10.1088/1741-2552/aace8c")
    corpus.add_paper(corpus_db, key="tangermann2012review",
                     title="Review of the BCI Competition IV",
                     authors="Tangermann, M.", year=2012,
                     doi="10.3389/fnins.2012.00055")

    # 5. 渲染带 {{agg:...}} 和 \cite{...} 的 markdown
    paper_src = tmp_path / "main.md"
    paper_src.write_text(
        "## Abstract\n"
        "We reproduce a CSP+LDA baseline on motor imagery under the "
        "within-session protocol.\n\n"
        "## Results\n"
        "The baseline reached {{agg:baseline_csp_lda.acc}} across seeds, "
        "in line with earlier filter-bank spatial-pattern work \\cite{ang2012csp}.\n",
        encoding="utf-8",
    )
    render.render_file(paper_src, build_dir, corpus_path=corpus_db,
                       results_path=results_db, config_path=RENDER_CFG,
                       tags={"baseline_csp_lda": run_ids})
    out_text = (build_dir / "main.md").read_text(encoding="utf-8")
    assert "±" in out_text  # agg 渲染出来了

    # 6. make check 全绿
    report = checker.run(
        build_dir, results_path=results_db, corpus_path=corpus_db,
        contract_path=contract_path, config_path=RENDER_CFG,
        report_path=tmp_path / "check_report.json",
    )
    print("\n" + report.summary())
    assert report.ok, f"checker 未全绿：{[c for c in report.checks if c['status']=='FAIL']}"


def test_smoke_reproduce_with_real_moabb(tmp_path):
    """接真 MOABB 的完整路径（离线自动 skip）。跑 3 seed null+csp_lda baseline。"""
    from tests._helpers import require_moabb

    require_moabb()
    import harness
    from core import corpus, render, results
    from harness.evaluate import default_pipelines, evaluate_dev

    data = tmp_path / "data"
    data.mkdir()
    results_db = data / "results.sqlite"
    results.init_db(results_db)
    contract = _build_contract()

    run_ids = []
    try:
        for seed in range(3):
            out = evaluate_dev(default_pipelines(), contract, seed=seed,
                               results_path=results_db)
            run_ids.append(out["csp_lda"])
    except AssertionError:
        raise
    except Exception as e:  # 数据集需联网下载 / ~/mne_data 不存在
        pytest.skip(f"MOABB 数据集不可用（需联网下载）：{type(e).__name__}: {e}")
    a = results.agg(run_ids, "acc", db=results_db)
    assert a.unwrap().n == 3
