"""SQLite storage: the append-only audit log, plus content and appeal records.

audit_log  one row per event (a classification or an appeal). Rows are only
           ever inserted, never updated or deleted.
content    one row per submission: its current label and status. The status
           changes when an appeal arrives, which is why it lives here and not
           in the log.
appeals    one row per appeal (at most one per piece of content).
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = "audit_log.db"

AUDIT_COLUMNS = {
    "content_id": "TEXT",
    "creator_id": "TEXT",
    "timestamp": "TEXT",
    "event_type": "TEXT",          # 'classification' or 'appeal'
    "attribution": "TEXT",
    "confidence": "REAL",
    "llm_score": "REAL",
    "heuristic_score": "REAL",
    "decided_by": "TEXT",
    "scoring_version": "TEXT",
    "status": "TEXT",
    "signals": "TEXT",             # JSON: both full signal outputs (classification only)
    "appeal_id": "TEXT",           # appeal and appeal_resolved
    "creator_reasoning": "TEXT",   # appeal only
    "decision": "TEXT",            # appeal_resolved only: 'upheld' or 'overturned'
    "reviewer_id": "TEXT",         # appeal_resolved only
    "reviewer_notes": "TEXT",      # appeal_resolved only
}

CONTENT_COLUMNS = {
    "content_id": "TEXT PRIMARY KEY",
    "creator_id": "TEXT",
    "text": "TEXT",
    "word_count": "INTEGER",
    "attribution": "TEXT",
    "confidence": "REAL",
    "label": "TEXT",
    "why": "TEXT",
    "decided_by": "TEXT",
    "lean": "TEXT",
    "scoring_version": "TEXT",
    "signals": "TEXT",             # JSON
    "status": "TEXT",              # 'classified' or 'under_review'
    "appeal_token_hash": "TEXT",   # SHA-256 of the appeal token; the token itself is never stored
    "created_at": "TEXT",
}

APPEAL_COLUMNS = {
    "appeal_id": "TEXT PRIMARY KEY",
    "content_id": "TEXT UNIQUE",   # one appeal per piece of content
    "creator_id": "TEXT",
    "creator_reasoning": "TEXT",
    "evidence": "TEXT",
    "created_at": "TEXT",
    "decision": "TEXT",            # set when a reviewer resolves it
    "reviewer_id": "TEXT",
    "reviewer_notes": "TEXT",
    "resolved_at": "TEXT",
}

JSON_FIELDS = ("signals",)


def now():
    return datetime.now(timezone.utc).isoformat()


def _ensure_table(conn, table, columns):
    # IF NOT EXISTS makes this safe on every startup. It won't add columns to a
    # table that already exists, so missing columns are added with ALTER TABLE:
    # existing rows are kept, with NULL in the new columns.
    column_sql = ", ".join(f"{name} {sql_type}" for name, sql_type in columns.items())
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({column_sql})")
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, sql_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def _insert(conn, table, columns, entry):
    # Columns are named explicitly so the INSERT doesn't depend on column order.
    # Missing keys are stored as NULL; dicts and lists are stored as JSON text.
    row = {
        name: json.dumps(entry[name]) if isinstance(entry.get(name), (dict, list)) else entry.get(name)
        for name in columns
    }
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + n for n in columns)})",
        row,
    )


def _to_dict(row):
    record = dict(row)
    for field in JSON_FIELDS:
        if record.get(field):
            record[field] = json.loads(record[field])
    return record


@contextmanager
def _connect():
    # "with conn" commits (or rolls back on error) but doesn't close the
    # connection, so close it explicitly.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with _connect() as conn:
        _ensure_table(conn, "audit_log", AUDIT_COLUMNS)
        _ensure_table(conn, "content", CONTENT_COLUMNS)
        _ensure_table(conn, "appeals", APPEAL_COLUMNS)
        # Entries written before event_type existed were all classifications.
        conn.execute("UPDATE audit_log SET event_type = 'classification' WHERE event_type IS NULL")


def log_event(entry):
    with _connect() as conn:
        _insert(conn, "audit_log", AUDIT_COLUMNS, {**entry, "timestamp": now()})


def save_submission(content, audit_entry):
    """Store a new submission and its classification log entry in one transaction."""
    with _connect() as conn:
        _insert(conn, "content", CONTENT_COLUMNS, content)
        _insert(conn, "audit_log", AUDIT_COLUMNS, {**audit_entry, "timestamp": now()})


def get_content(content_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM content WHERE content_id = ?", (content_id,)).fetchone()
        if row is None:
            return None
        record = _to_dict(row)
        appeal = conn.execute("SELECT * FROM appeals WHERE content_id = ?", (content_id,)).fetchone()
    record["appeal"] = dict(appeal) if appeal else None
    return record


def record_appeal(appeal, audit_entry):
    """Store an appeal, set the content to under_review, and log it, all in one transaction.

    Returns False (and changes nothing) if the content is no longer 'classified',
    e.g. a second appeal raced in first.
    """
    with _connect() as conn:
        updated = conn.execute(
            "UPDATE content SET status = 'under_review' WHERE content_id = ? AND status = 'classified'",
            (appeal["content_id"],),
        ).rowcount
        if updated == 0:
            return False
        _insert(conn, "appeals", APPEAL_COLUMNS, appeal)
        _insert(conn, "audit_log", AUDIT_COLUMNS, {**audit_entry, "timestamp": now()})
    return True


def get_appeal(appeal_id):
    """An appeal plus the content fields a resolution needs, or None."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT a.*, c.status, c.attribution, c.confidence, c.decided_by, c.scoring_version
            FROM appeals a JOIN content c ON c.content_id = a.content_id
            WHERE a.appeal_id = ?
            """,
            (appeal_id,),
        ).fetchone()
    return dict(row) if row else None


def resolve_appeal(appeal_id, content_id, new_status, resolution, audit_entry):
    """Record a reviewer's decision, move the content out of under_review, and log it,
    all in one transaction.

    Returns False (and changes nothing) if the content is no longer under_review,
    e.g. another reviewer resolved it first.
    """
    with _connect() as conn:
        updated = conn.execute(
            "UPDATE content SET status = ? WHERE content_id = ? AND status = 'under_review'",
            (new_status, content_id),
        ).rowcount
        if updated == 0:
            return False
        conn.execute(
            "UPDATE appeals SET decision = :decision, reviewer_id = :reviewer_id, "
            "reviewer_notes = :reviewer_notes, resolved_at = :resolved_at WHERE appeal_id = :appeal_id",
            {**resolution, "appeal_id": appeal_id},
        )
        _insert(conn, "audit_log", AUDIT_COLUMNS, {**audit_entry, "timestamp": now()})
    return True


def appeal_queue(status="under_review"):
    """Appeals whose content has the given status, oldest first, with everything a reviewer needs."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT a.appeal_id, a.created_at AS filed_at, a.creator_id, a.creator_reasoning, a.evidence,
                   a.decision, a.reviewer_id, a.reviewer_notes, a.resolved_at,
                   c.content_id, c.text, c.word_count, c.attribution, c.confidence, c.label, c.why,
                   c.decided_by, c.lean, c.scoring_version, c.signals, c.status, c.created_at AS submitted_at
            FROM appeals a JOIN content c ON c.content_id = a.content_id
            WHERE c.status = ?
            ORDER BY a.created_at ASC
            """,
            (status,),
        ).fetchall()
    return [_to_dict(row) for row in rows]


def read_log(limit=20, content_id=None):
    # appeal_filed is computed at read time from the appeals table, so every entry
    # shows whether its content has been appealed without stored rows ever being edited.
    query = """
        SELECT l.*, EXISTS (SELECT 1 FROM appeals a WHERE a.content_id = l.content_id) AS appeal_filed
        FROM audit_log l
    """
    params = []
    if content_id:
        query += " WHERE l.content_id = ?"
        params.append(content_id)
    query += " ORDER BY l.timestamp DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    entries = [_to_dict(row) for row in rows]
    for entry in entries:
        entry["appeal_filed"] = bool(entry["appeal_filed"])
    return entries
