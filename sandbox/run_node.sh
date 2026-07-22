#!/usr/bin/env bash
# 单节点执行脚本。见 IMPLEMENTATION-P2.md §4.1。
#
# 挂载策略（边界强制的第一层，assert_boundary_intact 是第二层双保险）：
#   harness/            -> :ro
#   core/               -> :ro
#   contracts/          -> :ro
#   data/moabb/         -> :ro   （MOABB 缓存，只读）
#   solution/           -> :rw   （唯一可写）
#   artifacts/<node>/   -> :rw   （输出目录）
#
# 网络：训练期默认 --network=none。需要下载数据集时用单独的准备阶段在启动前完成。
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
NODE_ID="${1:?usage: run_node.sh <node_id> <cmd...>}"
shift
IMAGE="${SANDBOX_IMAGE:-research-agent-sandbox:latest}"
ARTIFACTS_DIR="${REPO_ROOT}/artifacts/${NODE_ID}"
mkdir -p "${ARTIFACTS_DIR}"

exec docker run --rm \
  --network=none \
  --cpus="${SANDBOX_CPUS:-2}" \
  --memory="${SANDBOX_MEM:-4g}" \
  -v "${REPO_ROOT}/harness:/work/harness:ro" \
  -v "${REPO_ROOT}/core:/work/core:ro" \
  -v "${REPO_ROOT}/contracts:/work/contracts:ro" \
  -v "${REPO_ROOT}/data/moabb:/work/data/moabb:ro" \
  -v "${REPO_ROOT}/solution:/work/solution:rw" \
  -v "${ARTIFACTS_DIR}:/work/artifacts/${NODE_ID}:rw" \
  "${IMAGE}" \
  "$@"
