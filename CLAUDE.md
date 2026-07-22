# 项目约定

这是一个科研 agent 的**底座层**。它的全部价值在于一组不变式，
见 IMPLEMENTATION.md §1。任何时候实现方便性与不变式冲突，牺牲方便性。

## 每次改动前
- 读 IMPLEMENTATION.md §1（不变式）和 §2（禁止事项）
- 如果你的改动会让某条不变式变弱，停下来，先问

## 硬规则
- `core/` 不 import 任何 agent 框架
- 不给 Metric / Agg 加任何能进字符串的方法（搜索内部要裸 dev 分数用 `results.search_metric_value`）
- 不给 results 加 update / delete
- `harness/` 只增不改；改了要在 PR 里说明为什么，并接受历史 run 作废
- 新增依赖需要理由
- `loop/confirm.py`：`SearchVerdict` 永远不能出现 DONE/SUCCESS（I11）；
  给状态赋 `ConfirmState.TEST_ONCE` 的语句必须包在检查人工 approve 的分支里
  （`test_no_code_path_reaches_test_once_without_human_approval` 用 AST 强制）
- `dev_score` 是搜索内部量，绝不能出现在 `core/render.py` 或 `paper/`（`test_tree` 强制）
- claim registry 的 `source.kind` **只有三种**：contract / ablation / approved —— 没有第四种（I16/I17）
- 写作 agent **不给**结果库查询工具，只给生成好的 `results_summary`（防 HARKing）
- 图表确定性不达标就改图，不要放宽 `figures-check`（I19）
- `unverifiable` 引用不静默放行、不自动删句；列清单 + `citation_unverifiable` 签字（I21）

## 命令
- make test      跑全部测试
- make check     跑确定性检查器
- make render    渲染论文
- make reproduce 从零复现全部数字

## 环境说明
- 说明书要求 Python 3.11+。仓库根的 `.venv` 是 Python 3.11.4 环境
  （pydantic 2.13 / numpy / scikit-learn / pyyaml / requests 已装），用它跑：
  `make test PY=.venv/bin/python`。代码同时兼容 conda base 的 3.9.7，
  故 `core/contract.py` 的 pydantic 模型用 `typing.Optional/List` 而非 PEP604 的
  `X | None`（两者语义等价，且 3.9/3.11 都能跑）。
- MOABB 1.2.0 + MNE 已装进 conda base（3.9），与 numpy 2.0.2 兼容、可正常 import。
  数据集解析测试通过；两个"真跑 baseline"用例仍 skip，因为它们需要
  `~/mne_data` 目录 + 联网下载 BNCI2014_001 数据集。要真跑：
  `mkdir -p ~/mne_data` 后在有网环境执行 `make test`（或 `make reproduce`）。
- MOABB 测试用子进程探测可导入性（`tests/_helpers.py`）：若某环境下 `import moabb`
  段错误（曾因 numpy 1.26.4 的坏 BLAS 触发 SIGSEGV），会干净 skip 而非崩溃整个 pytest。
- `core/corpus.search`（OpenAlex）属联网模块；离线用 `add_paper` 直接入库（仍受 I5 约束）。
- `.venv`（3.11）不含 moabb，跑测试时那 3 个 MOABB 用例走 skip；conda base 则跑到 2 skip。

## 第二期（实验循环，IMPLEMENTATION-P2.md）状态
- 已完成并全绿：Phase 6（`sandbox/` + `loop/sentry.py`，S1–S8 + `assert_boundary_intact`）、
  Phase 7（`loop/tree.py`/`ledger.py`/`dedup.py`/`context.py` + `adapters/policy.py`）、
  Phase 8（`loop/confirm.py`，含核心验收 `test_confirm_rejects_pure_noise_improvement`）。
- Phase 9/10 脚手架已就绪：`loop/run_loop.py`（注入 proposer/evaluator_fn，离线可测，
  强制 I11/I12/sentry/预算）、`adapters/evaluator.py`、`loop/cost.py`、`adapters/aide_adapter.py`。
- 仅剩 **live-only** 两项（与 MOABB 同样按需在有网环境跑，不阻塞离线全绿）：
  1. `adapters.aide_adapter.build_proposer` 的真实绑定 —— 需 `pip install -U aideml`，
     按其真实 API 实现 draft/improve → Proposal；未装时 `AideUnavailable` 优雅报错，测试 skip。
  2. Phase 10 的 60 节点真实搜索 + `audit/search_report.md` —— 需 aideml + 数据集下载。
- 去重 embedding：装了 `sentence-transformers` 用 all-MiniLM-L6-v2，否则用确定性
  char-3gram 哈希向量离线回退（`loop/dedup.py`）；台账 compact 用依赖注入的 compactor，
  I15（证据 run_id 并集不丢）对任何 compactor 都强制。

## 第二期 live 依赖现状（conda base 3.9）
- **sentence-transformers 5.1.2 已装并打通**：`loop/dedup.py` 用 all-MiniLM-L6-v2 真实句向量，
  能识别语义等价的改写（`tests/test_dedup_live.py` 全过，非 skip）。要点：
  - `transformers` 会探测导入本机那份与 numpy 2.0 不兼容的旧 TensorFlow 而崩，
    `dedup` 已在导入前 `USE_TF=0/USE_FLAX=0`（自动，无需手设环境变量）。
  - HF 采用「缓存优先」：直接改 `huggingface_hub.constants.HF_HUB_OFFLINE=True` 走本地缓存
    （秒级、不触发受限网络的漫长重试）；缓存 miss 才联网下载一次。
  - `RA_DEDUP_BACKEND=fallback|st|auto` 可切后端；单测强制 fallback 保持快/确定。
- **aideml 0.2.2 已装（--no-deps）**：仅装了包体用于对齐真实 API（`aide.agent.Agent` 的
  `_draft/_improve/_debug` 产出 `Node.plan/.code`；`ExecutionResult`）。`adapters/aide_adapter.py`
  已按此真实 API 写好三处替换点。**真正 live 还需**：`pip install aideml`（装全 humanize/omegaconf/
  openai 等运行期依赖）+ 设 `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`；未装齐/无 key 时 `is_available()`
  为 False、`tests/test_aide_live.py` 与 Phase 10 真实搜索 skip（无法在无 key 环境完成）。
- 直接依赖解析安装 `aideml` 会触发 pip 回溯长时间卡死；用 `--no-deps` 装包体，
  或单独装其运行期依赖。

## 第三期（消融、写作与交付，IMPLEMENTATION-P3.md）状态
- 已完成并全绿：Phase 11–16（claim registry / 三套模板 / 消融 / 确定性图表 /
 写作编排+support_check+C11–C17 / 负面路径+disclosure+package）。
- 核心验收：`test_harking_attempt_blocked`、`test_negative_result_path_completes`、
 `test_figure_rerun_byte_identical`、`test_unverifiable_citations_listed_and_require_signoff`。
- 命令：`make figures` / `make figures-check`（进 `make check`）/ `make package`。

## 第四期（选题、审稿与闭环，IMPLEMENTATION-P4.md）状态
- 已完成并全绿：Phase 17–21（`ideation/` 选题流水线 + `review/` LLM 审稿 + `run_full.py` 闭环）。
- 新增不变式 I22–I27，硬规则如下：
 - **I24 生成器隔离**：`ideation/generate.generate` 签名里没有任何"其他想法"类参数，
  每个 (model, seed) 组合是独立 llm_fn 调用，已生成结果绝不回灌下一次调用
  （`test_generators_are_isolated` 强制）。不要"优化"成共享 context 的批量调用。
 - **I23 检索式新颖性**：`ideation/novelty`，evidence 为空 → verdict 强制 `unknown`（不是 novel）、
  该轴不评分；evidence 里每个 key 必须能 `corpus.has_resolved_id`。检索不到 ≠ 新颖。
 - **I22 填充闸门**：`ideation/fill`，缺任一必填字段 → `status="incomplete"`、不进 `rankable()`，
  但仍落 `idea_drafts` 表（`test_incomplete_idea_cannot_enter_pool` 强制）。
  `success_threshold` / `kill_criteria` 填不出来是特征不是 bug。
 - **I27 不合并**：`ideation/score.AxisScores` 只有五个轴字段，结构上没有 overall/total；
  `schema/ideas.sql` 的 `axis_scores.axis` 用 CHECK 约束禁止写入 'overall'
  （`test_axis_scores_never_merged_into_single_number` 强制）。不要加加权总分方法。
 - **I25 真实资源**：`ideation/feasibility` 的单次 run 时长取 `loop.cost.median_single_run_minutes`
  （结果库真实 wall_clock 中位数），机械部分与 LLM 部分在报告里分开呈现。
 - **I26 locator 必填**：`review/objection`，无合法 locator（L142/Table 2/Fig 3/claim:id/§4.2）
  的意见在 `review/panel` 里直接丢弃，不是降权（`test_objection_without_locator_discarded` 强制）。
 - **review 表无 score 字段**：`schema/review.sql` 结构上没有 score 列
  （`test_no_score_field_in_review_schema` 剥离注释后强制）；`Objection` 用 `extra="forbid"` 拒 score。
- 去重**直接 import `loop/dedup.py`**（`ideation/generate.dedup_ideas`），不写第二套
 （`test_dedup_reuses_phase2_module` 强制）。
- 未改动 `harness/`：可行性的"支持数据集"读 `harness.data._DATASET_ALIASES`，避免动 harness_hash（I6）。
- 命令：`make full`（`run_full.py` 闭环：领域→选题→契约→实验→消融→写作→审稿→打包）。
- **live 全流程需**：LLM key（OPENAI/ANTHROPIC）+ aideml + MOABB 数据集下载。缺任一时 `make full`
 走**离线确定性 demo**（假模型 + `record_run` 写基线），但走真实
 ideation/gates/render/checker/review/package 路径，闭环装配已被 `tests/test_full_pipeline.py` 验收。
- ideation/review 里所有 LLM 参与步骤（generate/novelty query+judge/effect_size/difficulty/redteam/review）
 一律以**依赖注入的可调用对象**传入 —— 离线用确定性替身可测，live 换真实后端即可。
