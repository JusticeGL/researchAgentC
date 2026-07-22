"""写作编排 + 引用检查测试。见 IMPLEMENTATION-P3.md §4.5、§4.8、§6 Phase 15。"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_CFG = REPO_ROOT / "config" / "render.yaml"
CLAIMS_YAML = REPO_ROOT / "paper" / "claims.yaml"


def test_writing_agent_has_no_results_query_tool():
    from writing import compose

    tools = compose.agent_tools()
    assert "query_runs" not in tools and "results" not in tools
    compose.assert_no_results_query_tool(tools)          # 白名单本身合法
    with pytest.raises(AssertionError):
        compose.assert_no_results_query_tool(tools + ["query_runs"])


def test_task_description_is_fixed_and_forbids_conclusions(sample_contract):
    from writing import compose

    desc = compose.writing_task_description(sample_contract, True, "positive")
    assert "不能提出契约里没有的结论" in desc
    assert sample_contract.hypothesis in desc


# ---------------------------------------------------------------------------
# support_check 落地（§4.8）
# ---------------------------------------------------------------------------
def _corpus(tmp_path):
    from core import corpus

    db = tmp_path / "corpus.sqlite"
    corpus.init_db(db)
    return db, corpus


def test_support_check_unverifiable_without_llm(tmp_path):
    db, corpus = _corpus(tmp_path)
    corpus.add_paper(db, key="closed1", title="A", authors="X", year=2020,
                     doi="10.1/x", oa_status="closed", abstract="some abstract")
    v = corpus.support_check(db, "本方法显著优于基线", "closed1")  # 无 entail_fn
    assert v.verdict == "unverifiable"


def test_support_check_verdicts_with_injected_llm(tmp_path):
    db, corpus = _corpus(tmp_path)
    corpus.add_paper(db, key="p1", title="A", authors="X", year=2020,
                     doi="10.1/x", oa_status="closed", abstract="CSP improves MI decoding.")

    def entail_supported(claim, text):
        return "supported", "摘要支持该论断"

    v = corpus.support_check(db, "CSP 改善运动想象解码", "p1",
                             entail_fn=entail_supported, use_cache=False)
    assert v.verdict == "supported"

    def entail_unsupported(claim, text):
        return "unsupported", "摘要不支持"

    v2 = corpus.support_check(db, "另一个论断", "p1",
                              entail_fn=entail_unsupported, use_cache=False)
    assert v2.verdict == "unsupported"

    def entail_bad(claim, text):
        return "maybe", "?"

    with pytest.raises(ValueError):
        corpus.support_check(db, "x", "p1", entail_fn=entail_bad, use_cache=False)


# ---------------------------------------------------------------------------
# 正面论文端到端：compose → render → checker 全绿（含 C11/C12/C16/C17）
# ---------------------------------------------------------------------------
def _record_tag(results, db, base_run_fields, contract_hash, harness_hash, accs, seed0=0):
    ids = []
    for i, acc in enumerate(accs):
        ids.append(results.record_run(
            db=db, metrics={"acc": acc},
            **base_run_fields(seed=seed0 + i, phase="dev",
                              contract_hash=contract_hash, harness_hash=harness_hash)))
    return ids


def test_positive_paper_passes_check(tmp_path, sample_contract, base_run_fields):
    import harness
    from core import checker, gates, render, results
    from loop import ablation, ledger
    from loop.confirm import ConfirmState
    from writing import compose, templates

    data = tmp_path / "data"
    data.mkdir()
    results_db = data / "results.sqlite"
    audit_db = data / "audit.sqlite"
    ledger_db = data / "ledger.sqlite"
    results.init_db(results_db)
    gates.init_db(audit_db)
    ledger.init_db(ledger_db)

    contract = sample_contract
    contracts_dir = tmp_path / "contracts"
    ch = contract.content_hash_value()   # reproduced_run_ids 不进 hash
    hh = harness.harness_hash()

    baseline = _record_tag(results, results_db, base_run_fields, ch, hh,
                           [0.68, 0.66, 0.69, 0.67, 0.70], seed0=0)
    main = _record_tag(results, results_db, base_run_fields, ch, hh,
                       [0.74, 0.75, 0.73, 0.76, 0.74], seed0=10)

    contract.baselines[0].reproduced_run_ids = list(baseline)
    assert contract.content_hash_value() == ch
    frozen = contract.freeze(out_dir=contracts_dir, write=True)
    contract_path = contracts_dir / f"{frozen.contract_id}.v{frozen.version}.json"

    # 消融 a1（预注册），走 run_ablation 打 ablation_id
    plan = ablation.plan_ablations(frozen)[0]
    seq = iter([0.62, 0.63, 0.61, 0.64, 0.62])

    def abl_eval(plan, seed):
        return results.record_run(
            db=results_db, metrics={"acc": next(seq)},
            **base_run_fields(seed=100 + seed, phase="dev",
                              contract_hash=ch, harness_hash=hh))
    abl = ablation.run_ablation(plan, frozen, results_db, abl_eval, n_seeds=5,
                                audit_db=audit_db)

    ledger.add_lesson(ledger_db, contract.contract_id,
                      text="把 CSP 分量数从 4 调到 12 在 dev 上没有稳定收益，视为死路。",
                      kind="deadend", evidence=baseline[:1])
    active = ledger.active_lessons(ledger_db, contract.contract_id)

    # compose 正面论文
    decision = templates.derive_decision(ConfirmState.DONE, test_passed=True)
    src_dir = tmp_path / "paper_src"
    src = src_dir / "main.md"
    compose.compose_paper(frozen, decision, contract_hash=ch, lessons=active,
                          out_path=src, confirm_terminal_state="DONE")
    meta_path = src.with_suffix(".md.meta.json")

    # 渲染
    build_dir = tmp_path / "build"
    tags = {"baseline": baseline, "main": main, "abl_a1": abl}
    render.render_file(src, build_dir, corpus_path=None, results_path=results_db,
                       config_path=RENDER_CFG, tags=tags)

    # checker 全绿
    report = checker.run(
        build_dir, results_path=results_db, corpus_path=None,
        contract_path=contract_path, config_path=RENDER_CFG,
        report_path=tmp_path / "report.json",
        claims_path=CLAIMS_YAML,
        patterns_path=REPO_ROOT / "config" / "claims_patterns.yaml",
        audit_path=audit_db, ledger_path=ledger_db, paper_meta_path=meta_path)
    fails = [c for c in report.checks if c["status"] == "FAIL"]
    print("\n" + report.summary())
    assert report.ok, f"checker 未全绿：{fails}"
    # 关键：C11/C12/C13/C16/C17 都要真的 PASS（不是 SKIP）
    by = {c["id"]: c["status"] for c in report.checks}
    for cid in ("C11", "C12", "C13", "C16", "C17"):
        assert by[cid] == "PASS", f"{cid} 不是 PASS：{by[cid]}"
