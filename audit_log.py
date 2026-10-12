import sqlite3
from datetime import datetime, timezone

DB_PATH = "audit_log.db"

COLUMNS = {
    "content_id": "TEXT",
    "creator_id": "TEXT",
    "timestamp": "TEXT",
    "attribution": "TEXT",
    "confidence": "REAL",
    "llm_score": "REAL",
    "heuristic_score": "REAL",
    "decided_by": "TEXT",
    "scoring_version": "TEXT",
    "status": "TEXT",
}

def init_db():
    # IF NOT EXISTS makes this safe on every startup. It won't add columns to a
    # table that already exists, so any column in COLUMNS that's missing is added
    # with ALTER TABLE: existing entries are kept, with NULL in the new columns.
    with sqlite3.connect(DB_PATH) as conn:
        column_sql = ", ".join(f"{name} {sql_type}" for name, sql_type in COLUMNS.items())
        conn.execute(f"CREATE TABLE IF NOT EXISTS audit_log ({column_sql})")
        existing = {row[1] for row in conn.execute("PRAGMA table_info(audit_log)")}
        for name, sql_type in COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE audit_log ADD COLUMN {name} {sql_type}")
        conn.commit()

def log_event(entry):
    # Columns are named explicitly so the INSERT doesn't depend on table column order.
    # Missing keys are stored as NULL.
    row = {name: entry.get(name) for name in COLUMNS}
    row["timestamp"] = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            f"INSERT INTO audit_log ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join(':' + name for name in COLUMNS)})",
            row,
        )

def read_log(limit=20):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]