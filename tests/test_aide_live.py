"""AIDE 集成的 live 测试。见 IMPLEMENTATION-P2.md §4.8、Phase 9。

需要装全 aideml 依赖（`pip install aideml`）且设置 OPENAI_API_KEY / ANTHROPIC_API_KEY。
当前环境用 --no-deps 只装了包体、且无 API key，故整文件 skip —— 与 MOABB/AIDE live 一致。
"""
import pytest


@pytest.fixture(autouse=True)
def _require_live():
    from adapters import aide_adapter

    if not aide_adapter.is_available():
        pytest.skip("aideml 运行期依赖未装齐（pip install aideml）")
    if not aide_adapter.has_llm_key():
        pytest.skip("未设置 LLM API key（OPENAI_API_KEY / ANTHROPIC_API_KEY）")


def test_proposer_maps_aide_node_to_proposal(sample_contract, tmp_path):
    """build_agent + build_proposer 能产出一个可执行的 Proposal（plan→desc, code→patch）。"""
    from pathlib import Path

    from adapters import aide_adapter
    from loop import run_loop

    cfg = run_loop.load_config(Path(__file__).resolve().parent.parent
                               / "config" / "loop.yaml")
    agent = aide_adapter.build_agent(sample_contract, cfg, workspace_dir=tmp_path)
    proposer = aide_adapter.build_proposer(agent, base_task_desc=sample_contract.question)

    proposal = proposer("（测试上下文）", existing_nodes=[], parent=None)
    assert isinstance(proposal.change_description, str)
    assert isinstance(proposal.patch, str) and proposal.patch.strip()
