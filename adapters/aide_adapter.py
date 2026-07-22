"""AIDE 集成。见 IMPLEMENTATION-P2.md §4.8。

已按**实际安装的 aideml 0.2.2** 的 API 写绑定（读了 aide/agent.py、journal.py、interpreter.py）：

  aide.agent.Agent
    - search_policy() -> Node | None
    - _draft() -> Node                         # 初始草案
    - _improve(parent: Node) -> Node           # 只读 parent.code
    - _debug(parent: Node) -> Node
    - step(exec_callback)                       # AIDE 自带主循环（我们不用它驱动）
  aide.journal.Node(code=..., plan=..., parent=...)
    - .plan  自然语言 sketch  → 我们的 change_description
    - .code  单文件 python    → 我们的 patch / solution
    - .stage_name ∈ {draft, debug, improve}
  aide.interpreter.ExecutionResult(term_out, exec_time, exc_type, exc_info, exc_stack)

三处替换（对应 §4.8）：
  1. 执行环境 → sandbox/run_node.sh（solution/ 可写、harness/ 只读、--network=none），
     取代 aide.interpreter.Interpreter.run。见 build_sandbox_exec_callback。
  2. 指标提取 → **不**用 AIDE 的 LLM 复核（agent.parse_exec_result 里那次 query）猜指标；
     脚本调 harness.evaluate_dev() 写 runs，我们从 runs 读回 dev_score（adapters/evaluator.py）。
     故在我们的 loop 里根本不走 AIDE 的 parse_exec_result。
  3. 提案钩子 → 用 Agent._draft/_improve 产出 Node，转成我们的 Proposal，
     在执行**之前**由 loop 插 dedup.check_duplicate（见 loop/run_loop.py）。

AIDE 的 Journal 不是真相来源：每个节点执行后我们往 tree_nodes 写自己的记录，
不一致以我们为准（这样换搜索器/升级 AIDE 都不影响历史数据）。

当前环境用 `pip install --no-deps aideml` 只装了包体（缺 humanize/omegaconf/openai 等运行期依赖，
且没有 LLM API key），因此 is_available() 为 False、相关测试 skip。要真正 live：
  .venv/bin/pip install aideml   # 装全依赖
  export OPENAI_API_KEY=...      # 或 ANTHROPIC_API_KEY
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


class AideUnavailable(RuntimeError):
    pass


def is_available() -> bool:
    """aideml 是否可导入（含其运行期依赖）。"""
    try:
        import aide  # noqa: F401

        return True
    except Exception:
        return False


def has_llm_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def _require_aide():
    if not is_available():
        raise AideUnavailable(
            "aideml 不可用（未安装或缺运行期依赖）。请 `pip install aideml` 装全依赖，"
            "并设置 OPENAI_API_KEY / ANTHROPIC_API_KEY 后再接入。"
            "在此之前 run_loop 可用注入的 proposer/evaluator_fn 离线运行/测试。")


def build_proposer(agent=None, base_task_desc: str = "") -> Callable:
    """把 AIDE 的 draft/improve 代码生成包成 run_loop 需要的 proposer。

    proposer(ctx, existing_nodes, parent) -> loop.run_loop.Proposal
      - parent 为 None：调 agent._draft()
      - 否则：用我们的 tree 节点造一个临时 aide Node（_improve 只读 .code）后调 agent._improve()
      - node.plan → change_description；node.code → patch
    每次调用把当轮 build_context 的结果并进 agent.task_desc，作为额外上下文注入 LLM 提示。

    需要真实 aideml + LLM key；不可用时抛 AideUnavailable。
    """
    _require_aide()
    if agent is None:
        raise AideUnavailable(
            "build_proposer 需要一个已构造好的 aide.agent.Agent（含 cfg 与 LLM 后端）。")

    from aide.journal import Node as ANode
    from loop.run_loop import Proposal

    def proposer(ctx: str, existing_nodes, parent):
        if base_task_desc or ctx:
            agent.task_desc = (base_task_desc + "\n\n# 额外上下文（本框架注入）\n" + ctx).strip()
        if parent is None:
            node = agent._draft()
        else:
            aide_parent = ANode(code=parent.patch, plan=parent.change_description)
            node = agent._improve(aide_parent)
        return Proposal(
            change_description=(node.plan or "").strip(),
            patch=node.code or "",
            parent_node_id=(parent.node_id if parent else None))

    return proposer


def build_agent(contract, cfg: dict, workspace_dir: Optional[Path] = None):
    """按 contract 造一个 aide.agent.Agent（供 build_proposer 使用）。

    走 aide 的配置管线（_load_cfg/prep_cfg）设 num_drafts/max_debug_depth/debug_prob，
    再构造 Agent(task_desc, cfg, journal)。需要真实 aideml。
    """
    _require_aide()
    from aide import Journal
    from aide.agent import Agent
    from aide.utils.config import _load_cfg, prep_cfg

    _cfg = _load_cfg(use_cli_args=False)
    _cfg.goal = contract.question
    _cfg.eval = f"{contract.primary_metric}（{contract.direction}），阈值 {contract.success_threshold}"
    if workspace_dir:
        _cfg.workspace_dir = str(workspace_dir)
    _cfg.agent.search.num_drafts = cfg["aide"]["num_drafts"]
    _cfg.agent.search.max_debug_depth = cfg["aide"]["max_debug_depth"]
    _cfg.agent.search.debug_prob = cfg["aide"]["debug_prob"]
    prepared = prep_cfg(_cfg)

    task_desc = f"{contract.question}\n\n假设：{contract.hypothesis}"
    return Agent(task_desc=task_desc, cfg=prepared, journal=Journal())


def run_node_sh() -> Path:
    """沙箱执行脚本路径（执行环境替换点）。"""
    return REPO_ROOT / "sandbox" / "run_node.sh"


def build_sandbox_exec_callback(node_id: str, entry: str = "solution/main.py") -> Callable:
    """返回 exec_callback(code, reset) -> aide.interpreter.ExecutionResult，用沙箱执行。

    把 code 写到 solution/ 下，用 sandbox/run_node.sh 在容器里 --network=none 跑，
    捕获 stdout/stderr 与耗时。替换 AIDE 默认的本地 Interpreter.run。
    需要 Docker 且已构建镜像；live-only。
    """
    _require_aide()
    from aide.interpreter import ExecutionResult
    import time

    def exec_callback(code: str, reset_session: bool = True) -> ExecutionResult:
        sol = REPO_ROOT / entry
        sol.parent.mkdir(parents=True, exist_ok=True)
        sol.write_text(code, encoding="utf-8")
        t0 = time.time()
        proc = subprocess.run(
            [str(run_node_sh()), node_id, "python", f"/work/{entry}"],
            capture_output=True, text=True, env={**os.environ, "REPO_ROOT": str(REPO_ROOT)})
        dt = time.time() - t0
        term_out = (proc.stdout or "").splitlines(keepends=True)
        err = proc.stderr or ""
        exc_type = None if proc.returncode == 0 else "SandboxNonZeroExit"
        if err:
            term_out += ["\n--- stderr ---\n"] + err.splitlines(keepends=True)
        return ExecutionResult(term_out=term_out, exec_time=dt, exc_type=exc_type,
                               exc_info=None, exc_stack=None)

    return exec_callback
