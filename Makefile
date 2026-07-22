# 科研 agent 底座层 —— 命令入口。见 IMPLEMENTATION.md §8。
# PY 可被覆盖：make test PY=python3.11
PY ?= python3
PYTEST ?= $(PY) -m pytest

.PHONY: test check render reproduce figures figures-check package full clean help

help:
	@echo "make test          跑全部测试"
	@echo "make check         跑确定性检查器（core.checker，含 figures-check）"
	@echo "make render        渲染论文（core.render）"
	@echo "make figures       重跑全部图，更新 out.pdf 与 manifest"
	@echo "make figures-check 重跑到临时目录比对 sha256（I19 byte-identical）"
	@echo "make reproduce     从零复现全部数字（tests/test_smoke_e2e.py）"
	@echo "make package       生成可复现交付包（writing.package）"
	@echo "make full          闭环：领域→选题→契约→实验→消融→写作→审稿→打包（run_full.py）"

test:
	$(PYTEST)

check: figures-check
	$(PY) -m core.checker

render:
	$(PY) -m core.render

figures:
	$(PY) -m figures build

figures-check:
	$(PY) -m figures check

package:
	$(PY) -m writing.package

full:
	$(PY) run_full.py

reproduce:
	$(PYTEST) tests/test_smoke_e2e.py -v

clean:
	rm -rf build/ data/*.sqlite audit/check_report.json
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
