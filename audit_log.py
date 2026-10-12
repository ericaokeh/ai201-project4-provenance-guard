import sqlite3
from datetime import datetime, timezone

DB_PATH = "audit_log.db"

def init_db():
    # Defines the columns (name + type), not row values. IF NOT EXISTS makes it
    # safe to call on every startup, but it won't add columns to an existing
    # table: delete audit_log.db after changing this schema.
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                content_id  TEXT,
                creator_id  TEXT,
                timestamp   TEXT,
                attribution TEXT,
                confidence  REAL,
                llm_score   REAL,
                status      TEXT
            )
        """)
        conn.commit()

def log_event(entry):
    # Columns are named explicitly so the INSERT doesn't depend on table column order.
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO audit_log (content_id, creator_id, timestamp, attribution, "
            "confidence, llm_score, status) VALUES (:content_id, :creator_id, "
            ":timestamp, :attribution, :confidence, :llm_score, :status)",
            {**entry, "timestamp": datetime.now(timezone.utc).isoformat()},
        )

def read_log(limit=20):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]