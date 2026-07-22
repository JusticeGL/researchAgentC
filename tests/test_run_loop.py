"""搜索主循环测试（离线，注入假 proposer/evaluator）。见 IMPLEMENTATION-P2.md Phase 9/10。"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOP_CFG = REPO_ROOT / "config" / "loop.yaml"


@pytest.fixture
def loop_env(tmp_path):
    from core import results
    from loop import ledger, sentry, tree

    results_path = tmp_path / "results.sqlite"
    tree_db = tmp_path / "tree.sqlite"
    ledger_db = tmp_path / "ledger.sqlite"
    sentry_db = tmp_path / "sentry.sqlite"
    results.init_db(results_path)
    tree.init_db(tree_db)
    ledger.init_db(ledger_db)
    sentry.init_db(sentry_db)
    return dict(results_path=results_path, tree_db=tree_db,
               ledger_db=ledger_db, sentry_db=sentry_db)


def _make_evaluator(loop_env, base_run_fields, acc_fn):
    from core import results

    counter = {}

    def evaluator_fn(proposal, seed, phase):
        desc = proposal.change_description
        counter[desc] = counter.get(desc, 0) + 1
        acc = acc_fn(desc, counter[desc])
        return results.record_run(
            db=loop_env["results_path"], metrics={"acc": acc},
            **base_run_fields(seed=seed, phase=phase, gpu_hours=0.0,
                              cost_usd=0.0, wall_clock_s=1.0))
    return evaluator_fn


def _make_proposer(patches=None):
    state = {"i": 0}

    def proposer(ctx, existing, parent):
        from loop.run_loop import Proposal

        i = state["i"]
        state["i"] += 1
        patch = (patches or {}).get(i, f"+ change {i}\n")
        return Proposal(change_description=f"change_{i}", patch=patch,
                        parent_node_id=None)
    return proposer


def _cfg():
    from loop import run_loop

    c = run_loop.load_config(LOOP_CFG)
    c["search"]["max_nodes"] = 5
    return c


def test_loop_creates_nodes_and_reads_dev_from_results(loop_env, base_run_fields,
                                                       sample_contract):
    from loop import run_loop, tree

    evaluator_fn = _make_evaluator(loop_env, base_run_fields, lambda d, n: 0.60)
    loop = run_loop.SearchLoop(
        sample_contract, cfg=_cfg(), proposer=_make_proposer(),
        evaluator_fn=evaluator_fn, **loop_env)
    report = loop.run()

    assert report.nodes_created == 5
    nodes = tree.query_nodes(loop_env["tree_db"], sample_contract.contract_id)
    assert len(nodes) == 5
    for n in nodes:
        assert n.run_ids            # I10
        assert n.dev_score is not None


def test_loop_never_redeems_test_token(loop_env, base_run_fields, sample_contract):
    """test_test_token_unused_after_full_search：整个自动搜索不碰测试集。"""
    import sqlite3

    from core import results
    from loop import run_loop

    # 即便签发了 token，自动循环也不会兑付
    results.issue_test_token(sample_contract.contract_id, db=loop_env["results_path"])
    evaluator_fn = _make_evaluator(loop_env, base_run_fields, lambda d, n: 0.60)
    loop = run_loop.SearchLoop(
        sample_contract, cfg=_cfg(), proposer=_make_proposer(),
        evaluator_fn=evaluator_fn, **loop_env)
    report = loop.run()

    assert report.test_token_redeemed is False
    con = sqlite3.connect(loop_env["results_path"])
    assert con.execute("SELECT COUNT(*) FROM holdout_access").fetchone()[0] == 0
    tok = con.execute(
        "SELECT redeemed_at FROM test_tokens WHERE contract_id=?",
        (sample_contract.contract_id,)).fetchone()
    con.close()
    assert tok[0] is None


def test_loop_continues_after_threshold_hit_and_flags_fluke(loop_env, base_run_fields,
                                                            sample_contract):
    """I11：某节点 dev 命中阈值后，搜索不终止；纯噪声被确认协议判 fluke。"""
    from loop import run_loop

    def acc_fn(desc, call_n):
        # change_2 第一次(节点自身)走运 0.90，之后确认 seed 全部 0.70 → fluke
        if desc == "change_2":
            return 0.90 if call_n == 1 else 0.70
        return 0.60

    evaluator_fn = _make_evaluator(loop_env, base_run_fields, acc_fn)
    loop = run_loop.SearchLoop(
        sample_contract, cfg=_cfg(), proposer=_make_proposer(),
        evaluator_fn=evaluator_fn, **loop_env)
    report = loop.run()

    assert report.nodes_created == 5           # 没有因命中阈值而提前停
    assert report.fluke_count >= 1             # 噪声被判 fluke
    assert report.test_token_redeemed is False


def test_loop_dedup_skips_repeated_proposals(loop_env, base_run_fields, sample_contract):
    from loop import run_loop

    def proposer(ctx, existing, parent):
        from loop.run_loop import Proposal
        return Proposal("完全相同的改动", "+ same\n", None)

    evaluator_fn = _make_evaluator(loop_env, base_run_fields, lambda d, n: 0.60)
    loop = run_loop.SearchLoop(
        sample_contract, cfg=_cfg(), proposer=proposer,
        evaluator_fn=evaluator_fn, **loop_env)
    report = loop.run()

    # 第一个建成节点，之后重复提案被 dedup 拦截（skip / hard_reject），不会建满 5 个
    assert report.nodes_created < 5
    assert report.dedup_skips + report.dedup_hard_rejects > 0


def test_loop_sentry_invalidates_cheating_patch(loop_env, base_run_fields, sample_contract):
    from core import results
    from loop import run_loop

    patches = {0: "def get_score():\n    return 0.99\n"}  # S4 硬编码指标
    evaluator_fn = _make_evaluator(loop_env, base_run_fields, lambda d, n: 0.60)
    loop = run_loop.SearchLoop(
        sample_contract, cfg=_cfg(), proposer=_make_proposer(patches),
        evaluator_fn=evaluator_fn, **loop_env)
    report = loop.run()

    assert report.invalidated >= 1
    # 被判作弊的 run 已作废
    invalid = results.query_runs(db=loop_env["results_path"],
                                 contract_id=sample_contract.contract_id,
                                 include_invalid=True)
    assert any(r.is_invalid for r in invalid)
