"""负面结果路径验收。见 IMPLEMENTATION-P3.md §5、§6 Phase 16。

核心：系统必须能自然地写出"假设未成立"的论文，而不是卡死或粉饰（I18）。
最有价值的情形：dev 上确认了、test 上没成立 —— 说明搜索过程过拟合。
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_CFG = REPO_ROOT / "config" / "render.yaml"
CLAIMS_YAML = REPO_ROOT / "paper" / "claims.yaml"
PATTERNS = REPO_ROOT / "config" / "claims_patterns.yaml"


def _record_runs(results, db, base_run_fields, contract_hash, harness_hash, accs, seed0=0):
    ids = []
    for i, acc in enumerate(accs):
        ids.append(results.record_run(
            db=db, metrics={"acc": acc},
            **base_run_fields(seed=seed0 + i, phase="dev",
                              contract_hash=contract_hash, harness_hash=harness_hash,
                              gpu_hours=0.1, cost_usd=0.5, wall_clock_s=60.0)))
    return ids


def test_negative_result_path_completes(tmp_path, sample_contract, base_run_fields):
    """构造 hypothesis_held=False → 选 negative 模板 → 写作 → 渲染 → check → package。

    人为把 threshold 调高到达不到（或 test 未过），走完整负面路径。
    这条路走不通 → 系统只能输出"我们成功了"，输出不可信。
    """
    import harness
    from core import checker, gates, render, results
    from loop import ledger
    from loop.confirm import ConfirmState
    from writing import disclosure, negative, package, templates

    data = tmp_path / "data"
    data.mkdir()
    results_db = data / "results.sqlite"
    audit_db = data / "audit.sqlite"
    ledger_db = data / "ledger.sqlite"
    results.init_db(results_db)
    gates.init_db(audit_db)
    ledger.init_db(ledger_db)

    # 把阈值调高，使"真实水平 ~0.70"达不到（负面结果）
    contract = sample_contract.model_copy(update={"success_threshold": 0.95})
    ch = contract.content_hash_value()
    hh = harness.harness_hash()

    baseline = _record_runs(results, results_db, base_run_fields, ch, hh,
                            [0.68, 0.66, 0.69, 0.67, 0.70], seed0=0)
    # 主实验：dev 看起来还行，但达不到 0.95
    main = _record_runs(results, results_db, base_run_fields, ch, hh,
                         [0.72, 0.71, 0.73, 0.70, 0.71], seed0=10)

    contract.baselines[0].reproduced_run_ids = list(baseline)
    contracts_dir = tmp_path / "contracts"
    frozen = contract.freeze(out_dir=contracts_dir, write=True)
    contract_path = contracts_dir / f"{frozen.contract_id}.v{frozen.version}.json"

    # 台账：死路 + fluke 教训（负面论文 Limitations 素材）
    ledger.add_lesson(ledger_db, frozen.contract_id,
                      text="把 CSP 分量数从 4 调到 12 在确认阶段被判 fluke。",
                      kind="deadend", evidence=main[:1])
    lessons = ledger.active_lessons(ledger_db, frozen.contract_id)

    # 终态：DONE 但 test 未达阈值 → hypothesis_held=False → negative 模板
    decision = templates.derive_decision(ConfirmState.DONE, test_passed=False)
    assert decision.hypothesis_held is False
    assert decision.template == "negative"

    src = tmp_path / "paper_src" / "main.md"
    out = negative.compose_negative_paper(
        frozen, results_db, ledger_db, contract_hash=ch, out_path=src,
        n_nodes=12, dev_score=0.90, test_score=0.71,
        terminal_state="DONE", test_passed=False)
    assert out["meta"]["template"] == "negative"
    assert "负面结果" in out["markdown"] or "未成立" in out["markdown"]
    # deadend 进了正文
    assert "fluke" in out["markdown"] or "死路" in out["markdown"] or "12" in out["markdown"]

    meta_path = src.with_suffix(".md.meta.json")
    build_dir = tmp_path / "build"
    tags = {"baseline": baseline, "main": main}
    render.render_file(src, build_dir, corpus_path=None, results_path=results_db,
                       config_path=RENDER_CFG, tags=tags)

    report = checker.run(
        build_dir, results_path=results_db, corpus_path=None,
        contract_path=contract_path, config_path=RENDER_CFG,
        report_path=tmp_path / "report.json",
        claims_path=CLAIMS_YAML, patterns_path=PATTERNS,
        audit_path=audit_db, ledger_path=ledger_db, paper_meta_path=meta_path)
    fails = [c for c in report.checks if c["status"] == "FAIL"]
    assert report.ok, f"负面路径 checker 未全绿：{fails}\n{report.summary()}"
    by = {c["id"]: c["status"] for c in report.checks}
    assert by["C16"] == "PASS"   # hypothesis_held=False ↔ negative
    assert by["C17"] == "PASS"   # Limitations 引用了台账

    # 披露 + 打包
    disc = disclosure.generate(frozen, audit_db, results_path=results_db,
                               agent_model="test-model",
                               out_path=tmp_path / "AI_CONTRIBUTION.md")
    assert "AI 参与度披露" in disc
    assert frozen.contract_id in disc
    assert "make reproduce" in disc

    pkg = package.package(tmp_path / "dist", contract_path=contract_path,
                          paper_build_dir=build_dir, audit_db=audit_db,
                          results_path=results_db, agent_model="test-model",
                          include_figures=True)
    assert (pkg / "AI_CONTRIBUTION.md").exists()
    assert (pkg / "README.md").exists()
    assert (pkg / "package_manifest.json").exists()
    assert (pkg / "contracts" / contract_path.name).exists()
    assert (pkg / "build" / "provenance.json").exists()


def test_dev_confirmed_test_failed_is_valuable_negative(sample_contract):
    """dev 过线、test 未过 → 明确指出过拟合（negative.py 素材）。"""
    from writing import negative

    s = negative.dev_test_gap_str(0.90, 0.71, threshold=0.75, direction="maximize")
    assert "过拟合" in s
    assert "0.90" in s and "0.71" in s


def test_disclosure_generated_from_audit_only(tmp_path, sample_contract):
    from core import gates
    from writing import disclosure

    audit = tmp_path / "audit.sqlite"
    gates.init_db(audit)
    gates.record_decision("g1", "contract_review", "c_test.v1", "hypothesis",
                          "edit", "改了措辞", db=audit)
    gates.record_decision("g1", "contract_review", "c_test.v1", "budget",
                          "reject", "预算太紧", db=audit)

    text = disclosure.generate(sample_contract, audit, agent_model="gpt-x")
    assert "改了措辞" in text
    assert "预算太紧" in text
    assert "gpt-x" in text
    # 不捏造结论
    assert "成功证明" not in text


def test_unverifiable_citations_listed_and_require_signoff(tmp_path, sample_contract,
                                                           base_run_fields):
    """C15/I21：unverifiable 引用不阻断 render，但 checker 要求签字。"""
    import harness
    from core import checker, corpus, gates, render, results
    from loop.confirm import ConfirmState
    from writing import compose, templates

    results_db = tmp_path / "results.sqlite"
    audit_db = tmp_path / "audit.sqlite"
    corpus_db = tmp_path / "corpus.sqlite"
    results.init_db(results_db)
    gates.init_db(audit_db)
    corpus.init_db(corpus_db)
    corpus.add_paper(corpus_db, key="closed2099", title="Paywalled", authors="X",
                     year=2099, doi="10.1/paywall", oa_status="closed")

    ch = sample_contract.content_hash_value()
    hh = harness.harness_hash()
    baseline = _record_runs(results, results_db, base_run_fields, ch, hh,
                            [0.7, 0.71, 0.69, 0.7, 0.72], seed0=0)
    main = _record_runs(results, results_db, base_run_fields, ch, hh,
                         [0.74, 0.75, 0.73, 0.76, 0.74], seed0=10)
    sample_contract.baselines[0].reproduced_run_ids = list(baseline)
    contracts_dir = tmp_path / "contracts"
    frozen = sample_contract.freeze(out_dir=contracts_dir, write=True)
    contract_path = contracts_dir / f"{frozen.contract_id}.v{frozen.version}.json"

    decision = templates.derive_decision(ConfirmState.DONE, test_passed=True)
    src = tmp_path / "paper.md"
    compose.compose_paper(frozen, decision, contract_hash=ch, out_path=src,
                          confirm_terminal_state="DONE")
    # 塞一条付费墙引用进论文
    text = src.read_text(encoding="utf-8")
    text += "\n\n相关工作见 \\cite{closed2099}。\n"
    src.write_text(text, encoding="utf-8")
    meta = src.with_suffix(".md.meta.json")

    # 正模板含 {{agg:abl_a1.acc}}，需提供消融 tag
    from loop import ablation

    plan = ablation.plan_ablations(frozen)[0]
    seq = iter([0.62, 0.63, 0.61, 0.64, 0.62])

    def abl_eval(plan, seed):
        return results.record_run(
            db=results_db, metrics={"acc": next(seq)},
            **base_run_fields(seed=200 + seed, phase="dev",
                              contract_hash=ch, harness_hash=hh))
    abl = ablation.run_ablation(plan, frozen, results_db, abl_eval, n_seeds=5,
                                audit_db=audit_db)

    build = tmp_path / "build"
    render.render_file(src, build, corpus_path=corpus_db, results_path=results_db,
                       config_path=RENDER_CFG,
                       tags={"baseline": baseline, "main": main, "abl_a1": abl})

    # 未签字 → C15 FAIL
    r1 = checker.run(build, results_path=results_db, corpus_path=corpus_db,
                     contract_path=contract_path, config_path=RENDER_CFG,
                     claims_path=CLAIMS_YAML, patterns_path=PATTERNS,
                     audit_path=audit_db, paper_meta_path=meta)
    by1 = {c["id"]: c for c in r1.checks}
    assert by1["C15"]["status"] == "FAIL"

    # 签字后 → C15 PASS
    gates.sign_unverifiable_citations(["closed2099"], "approve",
                                      reason="已知晓付费墙无法验证", db=audit_db)
    r2 = checker.run(build, results_path=results_db, corpus_path=corpus_db,
                     contract_path=contract_path, config_path=RENDER_CFG,
                     claims_path=CLAIMS_YAML, patterns_path=PATTERNS,
                     audit_path=audit_db, paper_meta_path=meta)
    by2 = {c["id"]: c["status"] for c in r2.checks}
    assert by2["C15"] == "PASS"
