# 单节点执行镜像。见 IMPLEMENTATION-P2.md §4.1。
# 镜像只负责提供解释器与依赖；只读/可写边界由 run_node.sh 在 `docker run` 时用 -v 施加。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/mpl

WORKDIR /work

# 依赖在构建期装好；训练期默认断网（--network=none），装不了新包。
COPY requirements-sandbox.txt /tmp/requirements-sandbox.txt
RUN pip install --no-cache-dir -r /tmp/requirements-sandbox.txt || true

# 非 root 运行，缩小越权写的可能
RUN useradd -m runner
USER runner

CMD ["python", "-c", "print('sandbox image ok')"]
