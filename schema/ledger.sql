-- 经验台账。见 IMPLEMENTATION-P2.md §4.4。
-- I15：台账压缩不得丢失证据 —— compact 前后 evidence run_id 的并集必须相等（应用层强制）。
-- 被合并的旧条目不删除，写 superseded_by。

CREATE TABLE IF NOT EXISTS lessons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   TEXT NOT NULL,
    text          TEXT NOT NULL,        -- 一句话
    kind          TEXT NOT NULL,        -- deadend|constraint|insight|bug_pattern
    evidence      TEXT NOT NULL,        -- JSON list of run_id
    created_at    TEXT NOT NULL,
    superseded_by INTEGER REFERENCES lessons(id)
);

-- lessons 不删除（合并用 superseded_by 表达）
CREATE TRIGGER IF NOT EXISTS lessons_no_delete BEFORE DELETE ON lessons
BEGIN SELECT RAISE(ABORT, 'lessons are append-only; use superseded_by'); END;
