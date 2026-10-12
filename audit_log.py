import sqlite3
from datetime import datetime, timezone

DB_PATH = "audit_log.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            "content_id": "3f7a2b1e-...",
              "creator_id": "test-user-1",
              "timestamp": "2025-04-01T14:32:10.123Z",
              "attribution": "likely_ai",
              "confidence": 0.78,
              "llm_score": 0.81,
              "status": "classified"
            )
        """)
        conn.commit()

def log_event(entry):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO audit_log VALUES (:content_id, :creator_id, :timestamp, "
            ":attribution, :confidence, :status)",
            {**entry, "timestamp": datetime.now(timezone.utc).isoformat()},
        )

def read_log(limit=20):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]