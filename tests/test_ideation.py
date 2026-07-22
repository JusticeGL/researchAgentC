"""第四期选题流水线测试。见 IMPLEMENTATION-P4.md §6。

三个验收核心：
  test_generators_are_isolated
  test_incomplete_idea_cannot_enter_pool
（第三个 test_objection_without_locator_discarded 在 test_review.py）
其余覆盖 I23/I25/I27 与 dedup 复用。
"""
import inspect
import os

import pytest

os.environ.setdefault("RA_DEDUP_BACKEND", "fallback")  # 测试用确定性离线 embedding

from core import corpus
from ideation import feasibility as feas_mod
from ideation import fill, generate, novelty, redteam, score, store


# ---------------------------------------------------------------------------
# 假 LLM / 假检索（离线可测）
# ---------------------------------------------------------------------------
def _fake_llm(model, prompt, temperature, seed):
    # 每个模型产出可区分的两条想法，彼此不同（利于测多样性/去重）
    tag = f"{model}:{seed}"
    return (f"想法A（{tag}）：在 BNCI2014_001 上用 CSP+LDA 做被试内运动想象分类。\n\n"
            f"想法B（{tag}）：在 SSVEP 上用黎曼几何特征提升跨被试泛化。")


# ---------------------------------------------------------------------------
# I24：生成器隔离
# ---------------------------------------------------------------------------
def test_generators_are_isolated():
    sig = inspect.signature(generate.generate)
    forbidden = {"other_ideas", "prior_ideas", "prior", "history", "context",
                 "previous", "peers", "seen", "all_ideas", "existing"}
    assert forbidden.isdisjoint(sig.parameters.keys()), \
        "generate() 的签名里不能有任何'其他想法'类参数（I24）"

    # 运行期：每次 llm_fn 调用拿到的 prompt 不含任何已生成想法的文本
    seen_prompts = []

    def recording_llm(model, prompt, temperature, seed):
        for p in seen_prompts:
            # 上一轮的 prompt 不应被塞进这一轮（且 prompt 不含"想法A/B"的产出）
            pass
        assert "想法A" not in prompt and "想法B" not in prompt, "prompt 泄漏了其他生成结果"
        seen_prompts.append(prompt)
        return _fake_llm(model, prompt, temperature, seed)

    ideas = generate.generate(seed_papers=["ang2012csp", "lotte2018review"],
                              domain="motor imagery", models=["m1", "m2", "m3"],
                              n_per_model=2, temperature=0.9, llm_fn=recording_llm)
    # 3 模型 × 2 种子 × 2 想法 = 12
    assert len(ideas) == 12
    assert all(isinstance(i, generate.RawIdea) for i in ideas)


def test_dedup_reuses_phase2_module(monkeypatch):
    """去重必须调用 loop/dedup.py，不写第二套。"""
    from loop import dedup

    called = {"n": 0}
    real = dedup.check_duplicate

    def spy(*args, **kwargs):
        called["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(dedup, "check_duplicate", spy)
    ideas = generate.generate(None, "mi", ["m1"], 2, 0.5, _fake_llm)
    kept = generate.dedup_ideas(ideas)
    assert called["n"] > 0, "dedup_ideas 必须走 loop.dedup.check_duplicate"
    assert len(kept) <= len(ideas)


# ---------------------------------------------------------------------------
# I23：新颖性检索，evidence 空 → unknown（不是 novel）
# ---------------------------------------------------------------------------
def test_novelty_empty_evidence_yields_unknown_not_novel(tmp_path):
    cdb = tmp_path / "corpus.sqlite"
    corpus.init_db(cdb)

    def empty_search(db, query, k):
        return []

    rep = novelty.novelty_gate("一个检索不到的新奇想法", cdb,
                               search_fn=empty_search)
    assert rep.verdict == "unknown"
    assert rep.scored is False
    assert rep.evidence == []


def test_novelty_with_hits_and_judge(tmp_path):
    cdb = tmp_path / "corpus.sqlite"
    corpus.init_db(cdb)
    corpus.add_paper(cdb, "ang2012csp", "CSP filter bank", "Ang", 2012,
                     doi="10.1/abc")
    corpus.add_paper(cdb, "other2020", "Unrelated", "X", 2020, arxiv_id="2001.00001")

    def search_fn(db, query, k):
        return ["ang2012csp", "other2020", "ghost_key"]  # ghost 无 ID，应被剔除

    def judge_fn(text, paper):
        return "different"

    rep = novelty.novelty_gate("some idea", cdb, search_fn=search_fn, judge_fn=judge_fn)
    assert set(rep.evidence) == {"ang2012csp", "other2020"}  # ghost_key 被 I23 剔除
    assert rep.verdict == "novel"
    assert rep.scored is True


# ---------------------------------------------------------------------------
# I25：可行性用结果库里真实 run 时长
# ---------------------------------------------------------------------------
def test_feasibility_uses_real_run_times_from_results(db_path, base_run_fields):
    from core import results

    # 写入 3 条真实 dev run，wall_clock_s = 60/120/180（中位 = 2 分钟）
    for wc in (60.0, 120.0, 180.0):
        results.record_run(db=db_path,
                           **base_run_fields(contract_id="c_feas", wall_clock_s=wc))

    class _Draft:
        contract_id = "c_feas"
        datasets = ["BNCI2014_001"]
        budget = None

    rep = feas_mod.feasibility(_Draft(), results_path=db_path)
    assert rep.single_run_minutes == 2.0, "单次 run 时长必须取结果库真实 wall_clock 中位数（I25）"
    assert rep.mechanical["single_run_minutes_source"] == "results_db_median"
    assert rep.mechanical["datasets_supported"] is True


def test_feasibility_unsupported_dataset_infeasible():
    class _Draft:
        contract_id = "c_x"
        datasets = ["NoSuchDataset999"]
        budget = None

    rep = feas_mod.feasibility(_Draft())
    assert rep.verdict == "infeasible"


# ---------------------------------------------------------------------------
# I22：填不满必填字段的想法不进池
# ---------------------------------------------------------------------------
def _complete_fields():
    return dict(
        question="q", hypothesis="h", datasets=["BNCI2014_001"],
        split_protocol="within_session", paradigm="motor_imagery",
        baselines=[{"name": "csp_lda", "cite_key": "ang2012csp", "impl": "moabb"}],
        primary_metric="acc", success_threshold=0.75, direction="maximize",
        n_seeds=5, kill_criteria=["dev acc < chance+0.05 连续 10 节点"],
        preregistered_ablations=[{"id": "a1", "description": "去 CSP",
                                  "falsifies": "性能来自空间滤波"}],
    )


def test_incomplete_idea_cannot_enter_pool(tmp_path):
    idb = tmp_path / "ideas.sqlite"
    store.init_db(idb)

    class _Novelty:
        evidence = ["ang2012csp"]
        notes = "ok"

    idea = generate.RawIdea("idea_x", "m1", None, "一个说不出成功阈值的想法")

    # 缺 success_threshold 与 kill_criteria
    fields = _complete_fields()
    fields.pop("success_threshold")
    fields["kill_criteria"] = []

    draft = fill.fill_contract(idea, _Novelty(), fields=fields)
    assert draft.status == "incomplete"
    assert "success_threshold" in draft.missing_fields
    assert "kill_criteria" in draft.missing_fields

    # 不进排名
    assert fill.rankable([draft]) == []

    # 但保留在 ideas 表里
    store.add_idea(idb, idea)
    store.add_draft(idb, draft)
    assert store.get_idea(idb, "idea_x") is not None
    assert len(store.query_drafts(idb, status="incomplete")) == 1
    assert store.query_drafts(idb, status="complete") == []


def test_complete_idea_enters_pool(tmp_path):
    class _Novelty:
        evidence = ["ang2012csp"]
        notes = "ok"

    idea = generate.RawIdea("idea_ok", "m1", None, "完整想法")
    draft = fill.fill_contract(idea, _Novelty(), fields=_complete_fields())
    assert draft.status == "complete"
    assert draft.missing_fields == []
    assert fill.rankable([draft]) == [draft]


# ---------------------------------------------------------------------------
# I27：五轴永不合并
# ---------------------------------------------------------------------------
def test_axis_scores_never_merged_into_single_number():
    field_names = set(score.AxisScores.__dataclass_fields__.keys())
    assert field_names == set(score.AXES), "AxisScores 只能有五个轴字段，不能有 overall/total"
    for banned in ("overall", "total", "combined", "score", "mean", "weighted"):
        assert banned not in field_names

    class _Nov:
        verdict = "novel"; scored = True; evidence = ["ang2012csp"]; notes = ""

    class _Feas:
        verdict = "feasible"; reasons = []; mechanical = {"datasets_supported": True}

    class _Draft:
        missing_fields = []

    scores = score.score(_Draft(), _Nov(), _Feas())
    rows = scores.as_rows()
    assert len(rows) == 5
    # effect_size 无估计器 → 不评分
    assert scores.effect_size.scored is False


def test_effect_size_requires_corpus_key():
    class _Draft:
        missing_fields = []

    class _Nov:
        verdict = "novel"; scored = True; evidence = []; notes = ""

    class _Feas:
        verdict = "feasible"; reasons = []; mechanical = {"datasets_supported": True}

    # 估计器不给 evidence → 不评分
    s1 = score.score(_Draft(), _Nov(), _Feas(),
                     effect_size_fn=lambda d: {"value": 0.8, "evidence": []})
    assert s1.effect_size.scored is False

    # 给了依据文献 → 评分
    s2 = score.score(_Draft(), _Nov(), _Feas(),
                     effect_size_fn=lambda d: {"value": 0.8, "evidence": ["ang2012csp"]})
    assert s2.effect_size.scored is True
    assert s2.effect_size.value == 0.8


# ---------------------------------------------------------------------------
# 红队：三次独立调用
# ---------------------------------------------------------------------------
def test_redteam_calls_are_independent(tmp_path):
    cdb = tmp_path / "corpus.sqlite"
    corpus.init_db(cdb)
    corpus.add_paper(cdb, "ang2012csp", "CSP", "Ang", 2012, doi="10.1/abc")

    calls = []

    def llm_fn(model, question, draft):
        calls.append((model, question))
        if "已经做过" in question:
            return "找到了 ang2012csp 做过类似的事。"
        return "某段对抗性回答。"

    class _Draft:
        contract_id = "c1"

    reports = redteam.red_team(_Draft(), cdb, llm_fn, models=["m1", "m2", "m3"])
    assert len(reports) == 3
    assert len(calls) == 3
    # 三个不同模型，互不相看（每次只传 question，不传别的回答）
    assert [c[0] for c in calls] == ["m1", "m2", "m3"]
    # Q2 解析出真实 corpus key
    q2 = next(r for r in reports if r.question_id == "Q2_prior_work")
    assert "ang2012csp" in q2.corpus_keys
