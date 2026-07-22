"""结果库单元测试。见 IMPLEMENTATION.md §5.1。"""
import pytest


def test_record_and_get_run(db_path, base_run_fields):
    from core import results

    rid = results.record_run(metrics={"acc": 0.72}, **base_run_fields())
    rec = results.get_run(rid)
    assert rec.contract_id == "c_test"
    assert rec.status == "ok"
    assert rec.is_invalid is False


def test_record_run_missing_field_raises(db_path):
    from core import results

    with pytest.raises(ValueError):
        results.record_run(contract_id="c")  # 缺一堆必填字段


def test_get_metric_unwrap_returns_float(db_path, base_run_fields):
    from core import results

    rid = results.record_run(metrics={"acc": 0.72}, **base_run_fields())
    m = results.get_metric(rid, "acc")
    assert isinstance(m, results.Metric)
    assert abs(m.unwrap() - 0.72) < 1e-9


def test_subject_metrics(db_path, base_run_fields):
    from core import results

    rid = results.record_run(
        subject_metrics={"S1": {"acc": 0.6}, "S2": {"acc": 0.8}},
        **base_run_fields(),
    )
    assert abs(results.get_metric(rid, "acc", subject="S1").unwrap() - 0.6) < 1e-9
    assert abs(results.get_metric(rid, "acc", subject="S2").unwrap() - 0.8) < 1e-9


def test_agg_stats(db_path, base_run_fields):
    from core import results

    rids = [
        results.record_run(metrics={"acc": v}, **base_run_fields(seed=i))
        for i, v in enumerate([0.6, 0.7, 0.8])
    ]
    a = results.agg(rids, "acc")
    stats = a.unwrap()
    assert stats.n == 3
    assert abs(stats.mean - 0.7) < 1e-9
    assert stats.std > 0
    assert stats.ci_low < stats.mean < stats.ci_high


def test_agg_excludes_invalidated(db_path, base_run_fields):
    from core import results

    rids = [
        results.record_run(metrics={"acc": v}, **base_run_fields(seed=i))
        for i, v in enumerate([0.6, 0.7, 0.99])
    ]
    results.invalidate(rids[2], reason="fluke")
    a = results.agg(rids, "acc")
    assert a.unwrap().n == 2  # 作废的那个不纳入


def test_query_runs_filters_and_excludes_invalid(db_path, base_run_fields):
    from core import results

    r_ok = results.record_run(**base_run_fields(phase="dev"))
    r_test = results.record_run(**base_run_fields(phase="test"))
    r_bad = results.record_run(**base_run_fields(phase="dev"))
    results.invalidate(r_bad, reason="boom")

    dev = results.query_runs(phase="dev")
    dev_ids = {r.run_id for r in dev}
    assert r_ok in dev_ids
    assert r_bad not in dev_ids  # 默认排除作废

    dev_all = results.query_runs(phase="dev", include_invalid=True)
    assert r_bad in {r.run_id for r in dev_all}
