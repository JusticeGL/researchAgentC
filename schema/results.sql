-- 结果库 schema。见 IMPLEMENTATION.md §5.1。
-- 不变式：
--   I1  runs / metrics append-only（trigger 强制 UPDATE/DELETE ABORT）
--   I6  run 作废用 run_invalidations 插记录表达，不 UPDATE runs
--   I7  测试集访问写 holdout_access

CREATE TABLE IF NOT EXISTS runs (
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

CREATE TABLE IF NOT EXISTS metrics (
    run_id  TEXT NOT NULL REFERENCES runs(run_id),
    subject TEXT,                     -- NULL 表示整体
    name    TEXT NOT NULL,
    value   REAL NOT NULL,
    PRIMARY KEY (run_id, subject, name)
);

CREATE TABLE IF NOT EXISTS holdout_access (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id TEXT NOT NULL,
    run_id      TEXT,
    token       TEXT NOT NULL,
    caller      TEXT NOT NULL,        -- 调用栈信息
    created_at  TEXT NOT NULL
);

-- 一次性 token 的签发记录。每个 contract 只能签发一次。
CREATE TABLE IF NOT EXISTS test_tokens (
    token       TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    issued_at   TEXT NOT NULL,
    redeemed_at TEXT,                 -- NULL 表示未使用
    UNIQUE (contract_id)              -- 每个 contract 只能签发一次
);

CREATE TABLE IF NOT EXISTS run_invalidations (   -- I6：runs 不能 UPDATE，作废往这张表插记录
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs(run_id),
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- I20（第三期）：消融 run 的标记。ablation_id 必须预注册（契约）或经 ablation_extension 批准。
-- 单独一张表而非 runs 的列：保持 runs 表 append-only 不受影响，且映射本身也 append-only。
CREATE TABLE IF NOT EXISTS ablation_runs (
    run_id      TEXT PRIMARY KEY REFERENCES runs(run_id),
    ablation_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ablation_runs_no_update BEFORE UPDATE ON ablation_runs
BEGIN SELECT RAISE(ABORT, 'ablation_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ablation_runs_no_delete BEFORE DELETE ON ablation_runs
BEGIN SELECT RAISE(ABORT, 'ablation_runs is append-only'); END;

-- I1：runs / metrics append-only
CREATE TRIGGER IF NOT EXISTS runs_no_update BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, 'runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS metrics_no_update BEFORE UPDATE ON metrics
BEGIN SELECT RAISE(ABORT, 'metrics is append-only'); END;
CREATE TRIGGER IF NOT EXISTS metrics_no_delete BEFORE DELETE ON metrics
BEGIN SELECT RAISE(ABORT, 'metrics is append-only'); END;
