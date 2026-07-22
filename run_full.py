"""闭环编排：领域描述 → 选题 → 契约 → 实验 → 消融 → 写作 → 审稿 → 打包。
见 IMPLEMENTATION-P4.md §7 Phase 21。

中间的人工卡点照常触发（topic_selection / contract_review / novelty_verdict /
citation_unverifiable 等；本脚本在非交互模式下自动 approve，真跑时应人工过）。

**真正的 live 全流程需要：** LLM key（OPENAI_API_KEY / ANTHROPIC_API_KEY）+ aideml +
MOABB 数据集下载。缺任一时本脚本走**离线确定性 demo**：用注入的假模型 / 直接
record_run 写基线，但走的是完全相同的 ideation / gates / render / checker / review /
package 代码路径 —— 因此闭环的**装配**是被真实验证过的，只是没接真实模型/数据。

`make full` 调它；离线环境下产出物落在 build/ 与 delivery/，且每阶段结束 `make check` 全绿。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent
RENDER_CFG = REPO_ROOT / "config" / "render.yaml"
CLAIMS_YAML = REPO_ROOT / "paper" / "claims.yaml"
CLAIMS_PATTERNS = REPO_ROOT / "config" / "claims_patterns.yaml"


# ---------------------------------------------------------------------------
# 离线确定性的假模型（live 时替换为真实后端）
# ---------------------------------------------------------------------------
def _fake_generate_llm(model, prompt, temperature, seed):
    return ("在 BNCI2014_001 上用 CSP+LDA 做被试内运动想象分类，"
            "对照 chance=0.25，成功阈值 0.75。\n\n"
            "在 SSVEP 上用黎曼几何提升跨被试泛化。")


def _fake_extract_fields(idea_text):
    """把想法文本抽成结构字段（live 用 LLM；此处确定性返回一份完整字段）。"""
    return dict(
        question="CSP+LDA 能否在 BNCI2014_001 上达到 0.75 被试内准确率？",
        hypothesis="空间滤波捕捉的判别性节律是分类性能主要来源。",
        datasets=["BNCI2014_001"], split_protocol="within_session",
        paradigm="motor_imagery",
        baselines=[{"name": "csp_lda", "cite_key": "ang2012csp", "impl": "moabb"}],
        primary_metric="acc", success_threshold=0.75, direction="maximize",
        n_seeds=5, kill_criteria=["dev acc 连续 10 节点低于 chance+0.05"],
        preregistered_ablations=[{"id": "a1", "description": "移除 CSP 只留原始通道",
                                  "falsifies": "性能来自空间滤波而非分类器"}],
        novelty_note="被试内协议下重新评估经典 baseline。",
    )


def _fake_search(db, query, k):
    return ["ang2012csp"]


def _fake_judge(idea_text, paper):
    return "different"


def _fake_effect_size(draft):
    return {"value": 0.06, "label": "small-medium", "evidence": ["ang2012csp"],
            "rationale": "同类空间滤波工作报告的被试内提升量级。"}


def _fake_difficulty(draft):
    return {"level": "low", "note": "CSP/LDA 与 MOABB 均开源，无难获取组件。"}


def _fake_redteam_llm(model, question, draft):
    if "已经做过" in question:
        return "最接近的是 ang2012csp，但其为跨被试协议，与本被试内计划不同。"
    if "最弱" in question:
        return "最弱一环：被试内协议下样本量偏小，方差可能较大。"
    return "必然失败的最强理由：若 CSP 分量选取不当，可能不敌原始通道基线。"


def _fake_review_fn(model, rendered_text):
    return [
        # 无法定位 → 必被丢弃（I26）
        {"locator": "overall", "kind": "clarity", "checkable": False,
         "statement": "整体写作质量有待提高。"},
        # 一条假的"没报告方差"意见 → autocheck 应驳回
        {"locator": "claim:mech_a1", "kind": "unsupported", "checkable": True,
         "statement": "该论断没有对应证据。", "suggested_check": "查 claim registry"},
    ]


# ---------------------------------------------------------------------------
# 闭环
# ---------------------------------------------------------------------------
def run_full(workdir: Path, domain: str = "motor imagery decoding",
             agent_model: str = "offline-demo") -> dict:
    """在 workdir 下跑完整闭环（离线确定性）。返回各阶段产物路径与关键结论。"""
    import harness
    from core import checker, corpus, gates, render, results
    from core.contract import (AblationSpec, BaselineSpec, Budget, Contract,
                               StatPlan)
    from ideation import (feasibility as feas_mod, fill, generate, novelty,
                          redteam, score, store)
    from loop import ablation, ledger
    from loop.confirm import ConfirmState
    from review import autocheck as ac
    from review import panel
    from writing import claims as claims_mod
    from writing import compose, package, templates

    workdir = Path(workdir)
    data = workdir / "data"
    data.mkdir(parents=True, exist_ok=True)
    results_db = data / "results.sqlite"
    corpus_db = data / "corpus.sqlite"
    audit_db = data / "audit.sqlite"
    ideas_db = data / "ideas.sqlite"
    ledger_db = data / "ledger.sqlite"
    review_db = data / "review.sqlite"
    for init, db in ((results.init_db, results_db), (corpus.init_db, corpus_db),
                     (gates.init_db, audit_db), (store.init_db, ideas_db),
                     (ledger.init_db, ledger_db), (panel.init_db, review_db)):
        init(db)
    corpus.add_paper(corpus_db, key="ang2012csp",
                     title="Filter Bank Common Spatial Pattern (FBCSP)",
                     authors="Ang, K. K.", year=2012,
                     doi="10.3389/fnins.2012.00039", oa_status="open")

    summary: dict = {"stages": []}

    # ---- 1) 选题：生成 → 去重 → 新颖性 → 可行性 → 填充 → 打分 → 红队 → 卡点 ----
    ideas = generate.generate(seed_papers=["ang2012csp"], domain=domain,
                              models=["m1", "m2", "m3"], n_per_model=2,
                              temperature=0.9, llm_fn=_fake_generate_llm)
    ideas = generate.dedup_ideas(ideas)
    for idea in ideas:
        store.add_idea(ideas_db, idea)
    top = ideas[0]

    nov = novelty.novelty_gate(top, corpus_db, search_fn=_fake_search,
                               judge_fn=_fake_judge)
    draft = fill.fill_contract(top, nov, extract_fn=_fake_extract_fields)
    feas = feas_mod.feasibility(draft, results_path=results_db,
                                difficulty_fn=_fake_difficulty)
    scores = score.score(draft, nov, feas, effect_size_fn=_fake_effect_size)
    store.add_draft(ideas_db, draft)
    store.add_axis_scores(ideas_db, draft.idea_id, scores)
    rt = redteam.red_team(draft, corpus_db, _fake_redteam_llm, models=["m1", "m2", "m3"])

    if draft.status != "complete":
        raise RuntimeError(f"选题产出的 draft 不完整：缺 {draft.missing_fields}")

    axis_rows = [{"axis": r.axis, "value": r.value, "label": r.label,
                  "scored": r.scored} for r in scores.as_rows()]
    topic_ok = gates.topic_selection(
        draft, axis_rows=axis_rows,
        novelty_top5=[corpus.get(corpus_db, k).title for k in nov.evidence],
        redteam=[{"q": r.question_id, "resp": r.response} for r in rt],
        db=audit_db)
    summary["stages"].append({"stage": "topic_selection", "approved": topic_ok,
                              "n_ideas": len(ideas), "draft_status": draft.status})

    # ---- 2) 契约：从 complete draft 组装冻结契约 ----
    contract = Contract(
        contract_id="c_full", version=1, parent_version=None,
        question=draft.question, hypothesis=draft.hypothesis,
        datasets=draft.datasets, split_protocol=draft.split_protocol,
        paradigm=draft.paradigm,
        baselines=[BaselineSpec(name=draft.baselines[0]["name"],
                                cite_key=draft.baselines[0]["cite_key"],
                                reproduced_run_ids=[])],
        primary_metric=draft.primary_metric,
        success_threshold=draft.success_threshold, direction=draft.direction,
        stat_plan=StatPlan(n_seeds=draft.n_seeds, test="paired_t",
                           correction="none", min_effect_size=0.02),
        budget=Budget(gpu_hours=10, usd=20, wall_clock_h=24, per_node_gpu_hours=2),
        kill_criteria=draft.kill_criteria,
        preregistered_ablations=[AblationSpec(**draft.preregistered_ablations[0])],
        novelty_evidence=draft.novelty_evidence, novelty_note=draft.novelty_note)

    ch = contract.content_hash_value()
    hh = harness.harness_hash()

    def _rec(accs, seed0):
        ids = []
        for i, acc in enumerate(accs):
            ids.append(results.record_run(
                db=results_db, metrics={"acc": acc}, contract_id=contract.contract_id,
                contract_hash=ch, harness_hash=hh, code_sha="0" * 40,
                config_hash="csp_lda", data_sha="bnci-fp", env_hash="env",
                seed=seed0 + i, split="within_session", phase="dev", status="ok"))
        return ids

    baseline = _rec([0.68, 0.66, 0.69, 0.67, 0.70], 0)
    main = _rec([0.74, 0.75, 0.73, 0.76, 0.74], 10)
    contract.baselines[0].reproduced_run_ids = list(baseline)
    assert contract.content_hash_value() == ch
    # §4.8：选题批准后进 novelty_verdict 卡点（只问"是否已有人做过"），再逐字段过契约
    gates.novelty_verdict(contract,
                          [corpus.get(corpus_db, k).title for k in nov.evidence],
                          decision="approve", db=audit_db)
    gates.review_contract(contract, db=audit_db)
    contracts_dir = workdir / "contracts"
    frozen = contract.freeze(out_dir=contracts_dir, write=True)
    contract_path = contracts_dir / f"{frozen.contract_id}.v{frozen.version}.json"
    summary["stages"].append({"stage": "contract_frozen",
                              "content_hash": frozen.content_hash[:16]})

    # ---- 3) 消融（预注册 a1）----
    plan = ablation.plan_ablations(frozen)[0]
    seq = iter([0.62, 0.63, 0.61, 0.64, 0.62])

    def abl_eval(plan, seed):
        return results.record_run(
            db=results_db, metrics={"acc": next(seq)}, contract_id=contract.contract_id,
            contract_hash=ch, harness_hash=hh, code_sha="0" * 40, config_hash="abl",
            data_sha="bnci-fp", env_hash="env", seed=100 + seed,
            split="within_session", phase="dev", status="ok")

    abl = ablation.run_ablation(plan, frozen, results_db, abl_eval, n_seeds=5,
                                audit_db=audit_db)

    # ---- 4) 写作：compose → render → check ----
    decision = templates.derive_decision(ConfirmState.DONE, test_passed=True)
    src = workdir / "paper_src" / "main.md"
    compose.compose_paper(frozen, decision, contract_hash=ch,
                          out_path=src, confirm_terminal_state="DONE")
    meta_path = src.with_suffix(".md.meta.json")

    build_dir = workdir / "build"
    tags = {"baseline": baseline, "main": main, "abl_a1": abl}
    render.render_file(src, build_dir, corpus_path=None, results_path=results_db,
                       config_path=RENDER_CFG, tags=tags)

    report = checker.run(
        build_dir, results_path=results_db, corpus_path=None,
        contract_path=contract_path, config_path=RENDER_CFG,
        report_path=workdir / "check_report.json", claims_path=CLAIMS_YAML,
        patterns_path=CLAIMS_PATTERNS, audit_path=audit_db, ledger_path=ledger_db,
        paper_meta_path=meta_path)
    if not report.ok:
        fails = [c for c in report.checks if c["status"] == "FAIL"]
        raise RuntimeError(f"make check 未全绿：{fails}")
    summary["stages"].append({"stage": "writing_checked", "check_ok": True})

    # ---- 5) 审稿：panel → autocheck ----
    rendered = (build_dir / "main.md").read_text(encoding="utf-8")
    registry = claims_mod.load_registry(CLAIMS_YAML)
    prov = None
    prov_path = build_dir / "provenance.json"
    if prov_path.exists():
        import json
        prov = json.loads(prov_path.read_text(encoding="utf-8"))
    review_out = panel.panel(
        "c_full", rendered, ["m1", "m2", "m3"], _fake_review_fn,
        registry_ids=registry.ids,
        ctx=ac.AutocheckContext(provenance=prov, registry=registry,
                                corpus_db=corpus_db),
        db=review_db)
    summary["stages"].append({
        "stage": "review",
        "kept": len(review_out["comments"]),
        "discarded": len(review_out["discarded"]),
        "autocheck": [c["autocheck_result"] for c in review_out["comments"]]})

    # ---- 6) 打包 ----
    delivery = workdir / "delivery"
    package.package(delivery, contract_path=contract_path,
                    paper_build_dir=build_dir, audit_db=audit_db,
                    results_path=results_db, agent_model=agent_model,
                    include_figures=False)
    summary["stages"].append({"stage": "package", "path": str(delivery)})
    summary["delivery"] = str(delivery)
    summary["build"] = str(build_dir)
    summary["review"] = review_out
    return summary


def _live_available() -> Optional[str]:
    """live 全流程是否具备条件。返回缺失原因（None 表示具备）。"""
    try:
        from adapters import aide_adapter
        if not aide_adapter.is_available():
            return "aideml/LLM 后端不可用（is_available()=False）"
    except Exception as e:
        return f"adapters.aide_adapter 不可用：{e}"
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        return "缺 OPENAI_API_KEY / ANTHROPIC_API_KEY"
    return None


def main(argv=None) -> int:  # pragma: no cover - CLI
    import tempfile

    reason = _live_available()
    if reason:
        print("=" * 70)
        print("live 全流程依赖缺失，走【离线确定性 demo】以验证闭环装配。")
        print(f"  原因：{reason}")
        print("  真跑需要：LLM key + aideml + MOABB 数据集下载（见文件头）。")
        print("=" * 70)
    workdir = Path(tempfile.mkdtemp(prefix="ra_full_"))
    summary = run_full(workdir)
    print("\n闭环完成，各阶段：")
    for st in summary["stages"]:
        print(f"  - {st}")
    print(f"\n产出：build={summary['build']}\n      delivery={summary['delivery']}")
    print("\n（离线 demo：走的是真实 ideation/gates/render/checker/review/package 路径，"
          "仅模型与数据为确定性替身。）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
