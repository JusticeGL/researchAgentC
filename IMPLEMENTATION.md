# 科研 agent 底座层 — 实现说明书

> 这份文档交给 Claude Code 执行。请先完整读完再动手。
> 本期**只做底座层**，不做 agent。做完的产物是：一套让"论文里的数字和引用不可能被伪造"的基础设施。

---

## 0. 一句话目标

建一个 Python 包，使得：

1. 任何进入论文的**数字**必须是结果库里某次真实实验的指针，LLM 在物理上无法直接写出一个数字
2. 任何进入论文的**引用**必须是文献库里已解析出 DOI/arXiv ID 的条目，`\cite{k}` 是外键
3. 评测代码 agent 改不了，改了的实验自动作废
4. 测试集在一次研究里只能被打开一次，且有日志

本期不接任何 agent。本期结束时，**人**手工写一份契约、手工跑一次 MOABB 实验、渲染出一份带真实数字和真实引用的 markdown，`make check` 全绿。

---

## 1. 不可协商的不变式

**这一节是整个项目的意义所在。任何时候如果实现方便性和这些不变式冲突，牺牲方便性。**

| # | 不变式 | 强制手段 |
|---|---|---|
| I1 | `runs` 表 append-only | SQLite trigger，UPDATE/DELETE 直接 ABORT |
| I2 | 指标值不可被字符串化 | `Metric.__str__` 和 `__format__` 抛 `TypeError` |
| I3 | 论文里的数字只能来自模板替换 | `render.py` 是唯一能 unwrap `Metric` 的地方 |
| I4 | `\cite{k}` 的 k 必须在 corpus 中 | 渲染前外键校验，失败则整个 render 失败 |
| I5 | 入库文献必须有 DOI 或 arXiv ID | `corpus.add()` 解析失败即拒绝，不存"疑似" |
| I6 | harness 内容变更使实验作废 | 每次 run 记录 `harness_hash`，校验不符则 run 标记 `invalid` |
| I7 | 测试集访问被记录且受限 | `evaluate_test()` 需要一次性 token，写 `holdout_access` 表 |
| I8 | 契约冻结后不可变 | 冻结时算 hash，任何修改必须新建版本，旧版本保留 |

**每一条不变式都必须有一个对应的测试，且测试先写。** 见 §6。

---

## 2. 明确不要做的事

Claude Code 在实现时很容易"贴心地"做出以下事情。全部禁止：

- ❌ **不要**给 `Metric` 加 `.value` 属性、`float()` 转换、或任何让它能进 f-string 的便利方法
- ❌ **不要**给 `results` 加 `update_run()` / `fix_metric()` / `delete_run()`，哪怕是"仅供调试"
- ❌ **不要**在 `harness/` 里写任何 agent 可调用的写操作
- ❌ **不要**为了让测试通过而放宽不变式；测试失败说明实现错了，不是测试错了
- ❌ **不要**引入 LangChain / LangGraph / 任何 agent 框架。本期的所有代码都是普通 Python 库
- ❌ **不要**做 Web UI。人工卡点是 CLI
- ❌ **不要**用 ORM。直接 `sqlite3`，schema 写在 `.sql` 文件里，看得见
- ❌ **不要**猜第三方 API。MOABB 和 PaperQA2 的接口请 `pip install` 后读实际源码或 `help()` 确认，本文档里的调用示例可能与实际版本不符

---

## 3. 技术栈

```
Python 3.11+
sqlite3            (标准库)
pydantic >= 2      契约 schema
moabb              BCI 数据集与评测协议
mne                MOABB 依赖
scikit-learn       baseline pipeline
paper-qa           文献检索 (PaperQA2)
pytest
```

不要加其他依赖，除非有不可替代的理由并在 PR 说明里写清楚。

---

## 4. 目录结构

```
research-agent/
├── CLAUDE.md                  # 见 §8，每次会话开始读
├── IMPLEMENTATION.md          # 本文档
├── pyproject.toml
├── Makefile
├── schema/
│   ├── results.sql
│   ├── corpus.sql
│   └── audit.sql
├── core/                      # 底座层。零框架依赖，纯 Python 库
│   ├── __init__.py
│   ├── contract.py
│   ├── corpus.py
│   ├── results.py
│   ├── render.py
│   ├── checker.py
│   └── gates.py
├── harness/                   # 冻结实验台。agent 只读
│   ├── __init__.py
│   ├── data.py
│   ├── evaluate.py
│   └── budget.py
├── solution/                  # 将来 agent 唯一可写区。本期只放 .gitkeep
│   └── .gitkeep
├── contracts/                 # 冻结的契约 JSON
├── paper/                     # 论文模板源文件
├── audit/                     # 卡点决策、holdout 日志、checker 报告
├── data/                      # 本地 DB 与 MOABB 缓存 (gitignore)
└── tests/
    ├── test_invariants.py     # 最重要的文件
    ├── test_results.py
    ├── test_render.py
    ├── test_corpus.py
    ├── test_harness.py
    └── test_smoke_e2e.py
```

---

## 5. 模块规格

### 5.1 `core/results.py` — 结果库

**Schema**（写进 `schema/results.sql`）

```sql
CREATE TABLE runs (
    run_id        TEXT PRIMARY KEY,
    parent_run_id TEXT,
    contract_id   TEXT NOT NULL,
    contract_hash TEXT NOT NULL,
    harness_hash  TEXT NOT NULL,
    code_sha      TEXT NOT NULL,      -- solution/ 的 git tree hash
    config_hash   TEXT NOT NULL,
    data_sha      TEXT NOT NULL,      -- 数据集指纹
    env_hash      TEXT NOT NULL,      -- pip freeze 的 hash
    seed          INTEGER NOT NULL,
    split         TEXT NOT NULL,      -- within_session|cross_session|cross_subject
    phase         TEXT NOT NULL,      -- dev|test
    status        TEXT NOT NULL,      -- ok|failed|invalid
    failure_class TEXT,               -- oom|not_converged|impl_error|data_error|other
    wall_clock_s  REAL,
    gpu_hours     REAL,
    cost_usd      REAL,
    artifacts_dir TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE metrics (
    run_id  TEXT NOT NULL REFERENCES runs(run_id),
    subject TEXT,                     -- NULL 表示整体
    name    TEXT NOT NULL,
    value   REAL NOT NULL,
    PRIMARY KEY (run_id, subject, name)
);

CREATE TABLE holdout_access (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    run_id     TEXT,
    token      TEXT NOT NULL,
    caller     TEXT NOT NULL,         -- 调用栈信息
    created_at TEXT NOT NULL
);

CREATE TABLE run_invalidations (        -- I6：runs 不能 UPDATE，作废用往这张表插记录表达
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(run_id),
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER runs_no_update BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, 'runs is append-only'); END;
CREATE TRIGGER runs_no_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'runs is append-only'); END;
CREATE TRIGGER metrics_no_update BEFORE UPDATE ON metrics
BEGIN SELECT RAISE(ABORT, 'metrics is append-only'); END;
CREATE TRIGGER metrics_no_delete BEFORE DELETE ON metrics
BEGIN SELECT RAISE(ABORT, 'metrics is append-only'); END;
```

> **注意 I6 与 append-only 的冲突**：run 需要能被标记 `invalid`，但表不能 UPDATE。
> 解法：不改原 run，往一张 `run_invalidations(run_id, reason, created_at)` 表插一条记录。
> 查询时 `runs LEFT JOIN run_invalidations`，有记录即视为无效。**不要**为此放开 UPDATE trigger。

**核心类型**

```python
class Metric:
    """指标值。刻意设计成无法进入字符串。"""
    __slots__ = ("run_id", "name", "subject", "_v")

    def __str__(self):
        raise TypeError(
            "Metric 不能被字符串化。论文中的数字必须写成 "
            "{{run:<run_id>.<metric>}} 模板，由 core.render 替换。"
        )

    __format__ = lambda self, spec: self.__str__()

    def __repr__(self):
        return f"<Metric {self.name}@{self.run_id[:8]}>"

    def unwrap(self) -> float:
        """只允许 core/render.py 调用。见 test_unwrap_callsites。"""
        return self._v


class Agg:
    """多 seed 聚合。同样不可字符串化。"""
    # mean / std / n / ci_low / ci_high
    # 论文默认只能引用 Agg，引用单次 run 必须显式标注
```

**API**（只有这些，不要加别的）

```python
def init_db(path: Path) -> None: ...
def record_run(**fields) -> str: ...                    # 返回 run_id
def get_run(run_id: str) -> RunRecord: ...
def get_metric(run_id: str, name: str, subject=None) -> Metric: ...
def agg(run_ids: list[str], name: str) -> Agg: ...
def query_runs(**filters) -> list[RunRecord]: ...
def invalidate(run_id: str, reason: str) -> None: ...
def issue_test_token(contract_id: str) -> str: ...      # 每个 contract 只能签发一次
def redeem_test_token(token: str, caller: str) -> None: # 用过即废，写 holdout_access
```

### 5.2 `core/render.py` — 模板渲染器

输入：`paper/*.md`（或 `.tex`）；输出：`build/*.md`，或抛异常。

支持的模板：

```
{{run:a3f1c9.test_acc}}              单次 run 的指标
{{agg:baseline_eegnet.test_acc}}     一组 run 的聚合（按 tag 查）
{{agg:main.test_acc|mean±std}}       指定格式
{{lit:0.72|cite=smith2019}}          从文献引用的数字，必须带 cite key
```

**渲染流程（顺序不能变）：**

1. 扫描全文所有 `\cite{...}` / `[@...]`，逐个查 corpus。任一 key 不存在 → 抛 `UnknownCitationError`，不产出任何输出
2. 替换所有 `{{...}}` 模板，来源是结果库。**替换时记录每个替换结果在输出文本里的字符区间（span）**，连同 run_id 一起进 `build/provenance.json`
3. **裸数字扫描**：对配置里指定的章节（默认 `## Results` / `## Abstract` / 所有表格块）做正则扫描，**但排除步骤 2 记录的替换 span——只在"非替换区域"里找数字**。命中未被模板替换的数字 → 抛 `BareNumberError`，报告行号
   - ⚠️ 顺序含义：替换后的正文里也全是数字（如 `0.72 ± 0.03`）。若不按 span 排除，扫描会把自己刚渲染出来的合法数字误判为裸数字，导致所有合法论文都被拒。**span 排除是这步能工作的前提**，不是可选优化
   - 白名单：`config/render.yaml` 里的 `allow_bare` 正则列表（年份、章节号、超参等）
   - 白名单**必须逐条写理由注释**，不允许 `.*`
4. 写出 `build/`；`build/provenance.json` 此时已含每个替换点的 run_id 与 span

`unwrap()` 只在本文件里被调用。`tests/test_invariants.py::test_unwrap_callsites` 用 `ast` 遍历整个仓库验证这一点。

### 5.3 `harness/` — 冻结实验台

**只读强制**（三层，都要有）：

1. `harness/` 单独一个 git 子模块或独立目录，运行时以 read-only 挂载给实验进程
2. `harness/__init__.py` 提供 `harness_hash()`：对 `harness/**/*.py` 的内容算 SHA256，排序后合并
3. 每次 `record_run` 必须传 `harness_hash`；`checker` 校验全库 run 的 harness_hash 是否一致，不一致要么说明有人改了 harness，要么说明跨版本，两种情况都必须显式处理

**`harness/data.py`** — MOABB 包装

```python
def get_paradigm(name: str): ...     # motor_imagery | p300 | ssvep
def get_dataset(name: str): ...
def data_fingerprint(dataset, paradigm) -> str: ...   # 进 runs.data_sha
```

> MOABB 的数据集类名在不同版本间变过（如 `BNCI2014001` vs `BNCI2014_001`）。
> **安装后读实际包确认**，不要照抄本文档。
> 划分协议对应 MOABB 的 `WithinSessionEvaluation` / `CrossSessionEvaluation` / `CrossSubjectEvaluation`。

**`harness/evaluate.py`** — 两级评测

```python
def evaluate_dev(pipelines: dict, contract, seed: int) -> dict:
    """搜索期随便调。只能碰 dev 划分。"""

def evaluate_test(pipelines: dict, contract, seed: int, token: str) -> dict:
    """需要一次性 token。redeem 后 token 作废。
    调用时把 inspect.stack() 的摘要写进 holdout_access.caller。"""
```

两者内部都必须：固定 seed（numpy / torch / random 全设）、调 MOABB evaluation、把结果直接写进 results 库并返回 `run_id`，**不返回裸 float**。

**`harness/budget.py`**

```python
@contextmanager
def budget(contract, node_id=None):
    """记 wall_clock / gpu_hours / 估算 cost。超单节点上限抛 BudgetExceeded。"""
```

### 5.4 `core/contract.py` — 契约

pydantic v2 model，字段如下（全部必填，除标注 optional）：

```python
class Contract(BaseModel):
    contract_id: str
    version: int
    parent_version: int | None

    question: str                       # 自然语言科研问题
    hypothesis: str                      # 可证伪的机制层陈述

    datasets: list[str]
    split_protocol: Literal["within_session", "cross_session", "cross_subject"]
    paradigm: str

    baselines: list[BaselineSpec]        # name + 引用 key + reproduced_run_ids
    primary_metric: str
    success_threshold: float
    direction: Literal["maximize", "minimize"]

    stat_plan: StatPlan                  # n_seeds, test, correction, min_effect_size
    budget: Budget                       # gpu_hours, usd, wall_clock_h, per_node_gpu_hours
    kill_criteria: list[str]
    preregistered_ablations: list[AblationSpec]   # 每条必须写明"它能证伪什么"
    novelty_evidence: list[str]          # corpus key 列表
    novelty_note: str                    # 与最近邻工作的差异说明

    frozen_at: str | None
    content_hash: str | None
```

关键方法：

```python
def content_hash(self) -> str:
    """对除 frozen_at / content_hash / baselines[*].reproduced_run_ids 外
    全部字段的规范化 JSON 求 hash。不依赖 frozen 状态，冻结前即可调用。
    reproduced_run_ids 被排除是刻意的——见下方生命周期说明。"""

def freeze(self) -> "Contract":
    """校验 reproduced_run_ids 非空 → 写入 content_hash() → 写
    contracts/{contract_id}.v{version}.json → model_config frozen=True。"""

def new_version(self, **changes) -> "Contract":
    """基于当前版本开新版本。旧文件永不删除。"""
```

**`BaselineSpec.reproduced_run_ids` 为空时，`freeze()` 必须报错。** baseline 数字必须是自己跑出来的，不是抄论文的。

> **生命周期顺序（打破循环依赖）**：freeze 要求 baseline 已复现，而 `record_run`
> 需要一个合法的 `contract_hash`——若 hash 把 `reproduced_run_ids` 也算进去，就成了
> "跑 baseline 要 hash、算 hash 又要 baseline 先跑完"的死结，且回填 run_id 还会反过来
> 改掉 hash。解法是把 `reproduced_run_ids` 排除在 `content_hash()` 之外，于是：
> 1. 填好除 `reproduced_run_ids` 外的全部字段
> 2. 调 `content_hash()` 得到**最终** hash（此后不再变）
> 3. 用这个 hash 作为 `contract_hash` 跑 baseline 复现，`record_run`
> 4. 把 run_id 回填进 `reproduced_run_ids`——不改变 hash
> 5. `freeze()` 校验非空并落盘，写出的 `content_hash` 与第 3 步 baseline run 里的一致

### 5.5 `core/corpus.py` — 文献库

```sql
CREATE TABLE papers (
    key         TEXT PRIMARY KEY,       -- bibtex key, e.g. lawhern2018eegnet
    doi         TEXT,
    arxiv_id    TEXT,
    title       TEXT NOT NULL,
    authors     TEXT NOT NULL,
    year        INTEGER NOT NULL,
    venue       TEXT,
    abstract    TEXT,
    oa_status   TEXT,                   -- open|closed|unknown
    fulltext_path TEXT,
    retrieved_at TEXT NOT NULL,
    query       TEXT
);
CREATE TABLE claim_support (
    claim_hash TEXT NOT NULL,
    key        TEXT NOT NULL REFERENCES papers(key),
    verdict    TEXT NOT NULL,           -- supported|partial|unsupported|unverifiable
    evidence   TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (claim_hash, key)
);
CREATE TRIGGER papers_require_id BEFORE INSERT ON papers
WHEN NEW.doi IS NULL AND NEW.arxiv_id IS NULL
BEGIN SELECT RAISE(ABORT, 'paper must have doi or arxiv_id'); END;
```

API：

```python
def search(query: str, k: int = 20) -> list[str]: ...   # 返回 key 列表，副作用是入库
def get(key: str) -> Paper: ...
def bibtex(keys: list[str]) -> str: ...
def support_check(claim: str, key: str) -> Verdict: ...
```

`search()` 内部走 PaperQA2 + OpenAlex/Crossref 解析 ID。**PaperQA2 开源版不含 Grobid 全文解析、非本地全文检索和引用遍历工具**，所以 `support_check` 对 `oa_status != "open"` 的文献只能返回 `unverifiable`。这是预期行为，不要假装能验证。

`unverifiable` 的数量必须在 checker 报告里单列，由人决定是否放行。

### 5.6 `core/checker.py` — 确定性检查器

`make check` 跑这个。全部是硬检查，没有 LLM 参与。

```
[ ] C1  build/ 中所有数字都有 provenance 记录
[ ] C2  所有 \cite{k} 都能在 corpus 解析出 DOI 或 arXiv ID
[ ] C3  所有"强论断"引用的 support_check == supported
        （强论断 = 匹配 config/claims.yaml 里的模式："优于"/"首次"/"SOTA"/"significantly"…）
[ ] C4  论文报告了 seed 方差与 n（每个 Agg 的 n >= contract.stat_plan.n_seeds）
[ ] C5  holdout_access 中该 contract 的记录数 <= 1；
        且"== 1"当且仅当论文报告了测试集数字（渲染结果里存在 test 相关的
        {{run:...}} / {{agg:...}}）。负面 / inconclusive / 未开测试集的论文
        必须是 0 条，此时出现任何测试集数字即 FAIL。
        （对应 I7"测试集至多打开一次"、P2 Phase 10"0 或 1 条"、
         P3 negative/inconclusive 模板"未开测试集"三处，不要写死 == 1）
[ ] C6  每张图有 manifest，且重跑一次结果 byte-identical
[ ] C7  baseline 数字与所引论文一致，或文中有显式的差异说明段落
[ ] C8  论文声明的 contract_hash 与 contracts/ 里的一致
[ ] C9  所有被引用 run 的 harness_hash 一致，且等于当前 harness_hash()
[ ] C10 没有被引用的 run 处于 invalid 状态
```

输出 `audit/check_report.json` + 人类可读摘要。任一 FAIL → 退出码非 0。

### 5.7 `core/gates.py` — 人工卡点

```bash
python -m core.gates list                    # 待处理卡点
python -m core.gates review <gate_id>        # 逐字段 approve/reject/edit
python -m core.gates history                 # 审计轨迹
```

交互形式：逐字段展示，每个字段要求 `[a]pprove / [r]eject / [e]dit`，reject 和 edit 必须填一行理由。**不接受"整体批准"**。

本期实现两个卡点类型：

- `contract_review` — 契约冻结前
- `novelty_verdict` — 独立一步，只问一个问题："这个想法是否已经有人做过？"并展示 `novelty_evidence` 里的 top-5 文献标题

决策写 `audit` 表，字段：`gate_id, gate_type, subject_id, field, decision, reason, decided_at`。

---

## 6. 测试（先写测试，再写实现）

`tests/test_invariants.py` 是本项目最重要的文件。每条不变式一个测试：

```python
def test_runs_append_only():
    # 直接 UPDATE 应抛 sqlite3.IntegrityError / OperationalError

def test_metric_str_raises():
    m = get_metric(rid, "acc")
    with pytest.raises(TypeError): str(m)
    with pytest.raises(TypeError): f"{m}"
    with pytest.raises(TypeError): "acc = %s" % m

def test_unwrap_callsites():
    # ast 遍历全仓库，断言 Metric.unwrap / Agg.unwrap 只在 core/render.py 被调用

def test_render_rejects_unknown_cite():
def test_render_rejects_bare_number_in_results():
def test_corpus_rejects_paper_without_id():
def test_contract_frozen_is_immutable():
def test_contract_freeze_requires_reproduced_baseline():
def test_test_token_single_use():
def test_holdout_access_logged():
def test_harness_hash_changes_on_edit():
    # 临时改 harness 里一个文件，断言 harness_hash() 变了
```

**null pipeline 检查**（`tests/test_harness.py`）：

```python
def test_null_pipeline_scores_at_chance():
    """一个只输出常数标签的 pipeline，准确率应在 1/n_classes 附近。
    不要写死 0.5：n_classes 从 paradigm/dataset 读取——
    BNCI2014001（BCI IV-2a）是 4 类，chance 是 0.25 而非 0.5，
    二分类数据集（如 BNCI2014004）才是 0.5。
    如果它显著高于 1/n_classes，说明评测泄漏了标签或划分有问题。"""
```

这个测试花一小时写，能挡住的问题比后面所有 LLM 审稿加起来都多。

---

## 7. 分阶段执行

每阶段结束跑 `make test`，全绿才进下一阶段。

**Phase 0 — 骨架**
- 目录结构、`pyproject.toml`、`Makefile`（`test` / `check` / `render` / `reproduce` 四个 target）
- `schema/*.sql` 全部写完
- `tests/test_invariants.py` 写完（此时全部失败，正常）
- 验收：`make test` 能跑起来并报告 N 个 failed

**Phase 1 — 结果库**
- `core/results.py` 全部实现
- 验收：I1 / I2 / I7 相关测试全绿

**Phase 2 — 冻结实验台**
- `harness/` 三个文件
- 先只支持一个数据集、一个 paradigm（建议从 MOABB 里被试数适中、下载量小的运动想象数据集起步，具体挑选见 `moabb.datasets` 实际列表）
- 至少一个真实 baseline pipeline（CSP + LDA，或 EEGNet）
- 验收：I6 测试绿、null pipeline 测试绿、能真跑出一个 run 并写进库

**Phase 3 — 契约与卡点**
- `core/contract.py` + `core/gates.py`
- 验收：I8 测试绿；能在 CLI 里逐字段过一遍契约并冻结

**Phase 4 — 文献库与渲染**
- `core/corpus.py` + `core/render.py` + `core/checker.py`
- 验收：I3 / I4 / I5 测试绿；`make check` 能跑出报告

**Phase 5 — 端到端 smoke**
- `tests/test_smoke_e2e.py`：从空库开始 → 建契约 → 跑 3 个 seed 的 baseline → 检索 3 篇文献 → 渲染一段带 `{{agg:...}}` 和 `\cite{...}` 的 markdown → `make check` 全绿
- 验收：`make reproduce` 从零跑通

**本期到此为止。接 AIDE 是下一期。**

---

## 8. `CLAUDE.md` 内容

在仓库根建 `CLAUDE.md`，内容：

```markdown
# 项目约定

这是一个科研 agent 的**底座层**。它的全部价值在于一组不变式，
见 IMPLEMENTATION.md §1。任何时候实现方便性与不变式冲突，牺牲方便性。

## 每次改动前
- 读 IMPLEMENTATION.md §1（不变式）和 §2（禁止事项）
- 如果你的改动会让某条不变式变弱，停下来，先问

## 硬规则
- `core/` 不 import 任何 agent 框架
- 不给 Metric / Agg 加任何能进字符串的方法
- 不给 results 加 update / delete
- `harness/` 只增不改；改了要在 PR 里说明为什么，并接受历史 run 作废
- 新增依赖需要理由

## 命令
- make test      跑全部测试
- make check     跑确定性检查器
- make render    渲染论文
- make reproduce 从零复现全部数字
```

---

## 9. 交给 Claude Code 时的提示词

> 读 IMPLEMENTATION.md。按 §7 的阶段顺序执行，从 Phase 0 开始。
> 每个 Phase 结束后停下来，跑 `make test`，把结果告诉我，等我确认再继续。
> 遇到 MOABB 或 PaperQA2 的 API 与文档描述不符时，以实际安装的包为准，
> 并在 IMPLEMENTATION.md 里改正对应段落。
> 不要跳过 §6 的测试先行。不要为了让测试过而改测试。

---

## 10. 本期不做的事（记下来，别让它偷偷混进来）

- AIDE 集成、树搜索、任何自动实验循环
- 选题阶段（C1）—— 第一份契约由你手写
- 论文写作 agent
- LLM 审稿
- Web UI
- 消融自动设计
