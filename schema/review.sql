-- LLM 审稿。见 IMPLEMENTATION-P4.md §5。
-- I26：没有 locator 的意见直接丢弃 —— 只有带合法 locator 的意见才会被 INSERT
--      （应用层 review/panel.py 在入库前丢弃无 locator 的意见；locator NOT NULL DB 兜底）。
-- 关键：**本表没有 score 字段**。LLM 审稿人校准差、更多在给行文流畅度打分，
--      不存、不算、不展示聚合分（§5.1）。test_no_score_field_in_review_schema 读本文件强制。
-- review_comments append-only（禁止 DELETE）。

CREATE TABLE IF NOT EXISTS review_comments (
    comment_id      TEXT PRIMARY KEY,
    paper_id        TEXT NOT NULL,
    locator         TEXT NOT NULL,   -- 必填且可定位：L142|Table 2|Fig 3|claim:id|§4.2
    kind            TEXT NOT NULL CHECK (kind IN
                      ('factual', 'unsupported', 'missing_control', 'clarity', 'novelty')),
    checkable       INTEGER NOT NULL DEFAULT 0,
    statement       TEXT NOT NULL,
    suggested_check TEXT,
    autocheck_result TEXT,           -- upheld|rejected|not_convertible|null（未跑）
    model           TEXT,            -- 产生它的审稿模型
    created_at      TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS review_comments_no_delete BEFORE DELETE ON review_comments
BEGIN SELECT RAISE(ABORT, 'review_comments is append-only'); END;
