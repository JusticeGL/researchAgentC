-- 审计库 schema。见 IMPLEMENTATION.md §5.7。
-- 人工卡点决策逐字段落库；append-only（决策不可篡改）。

CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_id    TEXT NOT NULL,
    gate_type  TEXT NOT NULL,         -- contract_review|novelty_verdict|...
    subject_id TEXT NOT NULL,         -- 被审对象（如 contract_id）
    field      TEXT NOT NULL,         -- 逐字段决策；整体决策用 '__gate__'
    decision   TEXT NOT NULL,         -- approve|reject|edit
    reason     TEXT,                  -- reject / edit 必须非空（应用层强制）
    decided_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;

-- 待处理卡点队列。卡点被创建时入队，review 解决后写 audit 并置为 resolved。
CREATE TABLE IF NOT EXISTS gate_queue (
    gate_id     TEXT PRIMARY KEY,
    gate_type   TEXT NOT NULL,        -- contract_review|novelty_verdict|...
    subject_id  TEXT NOT NULL,
    payload     TEXT NOT NULL,        -- JSON：待审字段与展示信息
    status      TEXT NOT NULL,        -- pending|approved|rejected
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);
