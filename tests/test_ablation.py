"""消融测试。见 IMPLEMENTATION-P3.md §4.3、§6 Phase 13。"""
import pytest


@pytest.fixture
def audit_db(tmp_path):
    from core import gates

    p = tmp_path / "audit.sqlite"
    gates.init_db(p)
    return p


def _evaluator(db_path, base_run_fields):
    from core import results

    def evaluator_fn(plan, seed):
        return results.record_run(db=db_path, metrics={"acc": 0.7 - 0.05 * seed % 2},
                                  **base_run_fields(seed=seed, phase="dev"))
    return evaluator_fn


def test_plan_only_from_preregistered(sample_contract):
    from loop import ablation

    plans = ablation.plan_ablations(sample_contract)
    assert [p.id for p in plans] == ["a1"]
    assert plans[0].falsifies


def test_run_ablation_tags_run_ids(db_path, base_run_fields, sample_contract):
    from loop import ablation

    plan = ablation.plan_ablations(sample_contract)[0]
    run_ids = ablation.run_ablation(plan, sample_contract, db_path,
                                    _evaluator(db_path, base_run_fields), n_seeds=5)
    assert len(run_ids) == 5
    tagged = ablation.ablation_run_ids(db_path, "a1")
    assert set(tagged) == set(run_ids)


def test_ablation_not_preregistered_rejected(db_path, base_run_fields, sample_contract):
    from loop import ablation

    bogus = ablation.AblationPlan("a99", "未注册消融", "什么都证伪不了")
    with pytest.raises(ValueError):
        ablation.run_ablation(bogus, sample_contract, db_path,
                              _evaluator(db_path, base_run_fields))


def test_ablation_extension_requires_falsification_field(audit_db):
    from loop import ablation

    # falsifies 为空 → 拒绝
    with pytest.raises(ValueError):
        ablation.request_ablation_extension(
            "c_test", "a_new", checks="检验X", falsifies="  ",
            decision="approve", audit_db=audit_db)


def test_ablation_extension_approves_and_enables(db_path, base_run_fields,
                                                 sample_contract, audit_db):
    from loop import ablation

    # 批准一个扩展消融
    ok = ablation.request_ablation_extension(
        "c_test", "a_new", checks="移除 whitening", falsifies="性能来自 whitening",
        decision="approve", reason="写作阶段发现证据不足", audit_db=audit_db)
    assert ok is True
    assert "a_new" in ablation.approved_ablation_ids(audit_db)

    # 现在 a_new 可以跑
    plan = ablation.AblationPlan("a_new", "移除 whitening", "性能来自 whitening")
    run_ids = ablation.run_ablation(plan, sample_contract, db_path,
                                    _evaluator(db_path, base_run_fields),
                                    n_seeds=3, audit_db=audit_db)
    assert len(run_ids) == 3
    assert ablation.validate_ablation_runs(db_path, sample_contract, audit_db) == []


def test_validate_ablation_runs_flags_illegal(db_path, base_run_fields, sample_contract):
    from core import results
    from loop import ablation

    # 直接塞一条非法 ablation_id 的映射（绕过 run_ablation）
    rid = results.record_run(db=db_path, metrics={"acc": 0.7},
                             **base_run_fields(phase="dev"))
    import sqlite3

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO ablation_runs (run_id, ablation_id, contract_id, created_at) "
                "VALUES (?, 'a_illegal', 'c_test', 't')", (rid,))
    con.commit()
    con.close()
    bad = ablation.validate_ablation_runs(db_path, sample_contract, audit_db=None)
    assert rid in bad
