-- 作弊探测事件。见 IMPLEMENTATION-P2.md §4.7。
-- 误报是可接受的成本。severity: halt(熔断停机) | invalidate(该 run 作废)。

CREATE TABLE IF NOT EXISTS sentry_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    TEXT NOT NULL,
    rule       TEXT NOT NULL,        -- S1..S8
    severity   TEXT NOT NULL,        -- halt|invalidate
    detail     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS sentry_events_no_delete BEFORE DELETE ON sentry_events
BEGIN SELECT RAISE(ABORT, 'sentry events are permanent'); END;
