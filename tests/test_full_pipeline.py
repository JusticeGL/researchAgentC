"""Phase 21 闭环验收。见 IMPLEMENTATION-P4.md §7 Phase 21。

离线确定性地跑通：领域 → 选题 → 契约 → 实验 → 消融 → 写作 → 审稿 → 打包。
走的是真实 ideation/gates/render/checker/review/package 代码路径，
仅模型与数据集为确定性替身（与 test_smoke_e2e 的离线策略一致）。
"""
import os
from pathlib import Path

os.environ.setdefault("RA_DEDUP_BACKEND", "fallback")


def test_full_pipeline_runs_offline(tmp_path):
    import run_full

    summary = run_full.run_full(tmp_path, agent_model="offline-demo")
    stages = {s["stage"]: s for s in summary["stages"]}

    # 各阶段都完成
    for name in ("topic_selection", "contract_frozen", "writing_checked",
                 "review", "package"):
        assert name in stages, f"缺阶段：{name}"

    # 选题：卡点通过，draft 完整
    assert stages["topic_selection"]["approved"] is True
    assert stages["topic_selection"]["draft_status"] == "complete"

    # 写作：make check 全绿（run_full 内部若不绿会抛异常）
    assert stages["writing_checked"]["check_ok"] is True

    # 审稿：无 locator 的意见被丢弃（I26），且 autocheck 驳回了假意见
    assert stages["review"]["discarded"] >= 1
    assert "rejected" in stages["review"]["autocheck"]

    # 交付包存在且含关键产物
    delivery = Path(summary["delivery"])
    assert (delivery / "AI_CONTRIBUTION.md").exists()
    assert (delivery / "contracts").exists()
    assert (delivery / "build").exists()


def test_full_pipeline_gates_recorded(tmp_path):
    """五个人工卡点里，选题与契约卡点应在 audit 表留痕。"""
    import sqlite3

    import run_full

    run_full.run_full(tmp_path)
    audit_db = tmp_path / "data" / "audit.sqlite"
    con = sqlite3.connect(audit_db)
    try:
        types = {r[0] for r in con.execute(
            "SELECT DISTINCT gate_type FROM audit").fetchall()}
    finally:
        con.close()
    assert "topic_selection" in types
    assert "contract_review" in types
