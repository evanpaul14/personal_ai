import sqlite3
import uuid
from contextlib import contextmanager
from config import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT 'New Chat',
    system_prompt TEXT,
    model_id      TEXT NOT NULL,
    created_at    DATETIME DEFAULT (datetime('now')),
    updated_at    DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK(role IN ('user','assistant','tool')),
    content          TEXT,
    reasoning        TEXT,
    tool_calls       TEXT,
    tool_call_id     TEXT,
    image_path       TEXT,
    created_at       DATETIME DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF content ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
    INSERT INTO messages_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
"""

def _ensure_fts_schema(db):
    # Older installs created an incompatible FTS schema with extra columns.
    # Recreate the virtual table/triggers so search works against existing DBs.
    cols = [r["name"] for r in db.execute("PRAGMA table_info(messages_fts)").fetchall()]
    if cols == ["content"]:
        return

    db.executescript("""
DROP TRIGGER IF EXISTS messages_ai;
DROP TRIGGER IF EXISTS messages_ad;
DROP TRIGGER IF EXISTS messages_au;
DROP TABLE IF EXISTS messages_fts;

CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='rowid'
);

CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;

CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
END;

CREATE TRIGGER messages_au AFTER UPDATE OF content ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
    INSERT INTO messages_fts(rowid, content)
    VALUES (new.rowid, new.content);
END;
""")

def _migrate(db):
    existing = {r["name"] for r in db.execute("PRAGMA table_info(messages)").fetchall()}
    if "reasoning" not in existing:
        db.execute("ALTER TABLE messages ADD COLUMN reasoning TEXT")

def init_db():
    with get_db() as db:
        db.executescript(SCHEMA)
        _ensure_fts_schema(db)
        _migrate(db)
        # Rebuild FTS index to cover any messages inserted before triggers existed
        try:
            db.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        except Exception:
            pass

@contextmanager
def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def new_id():
    return str(uuid.uuid4())

# --- Conversation helpers ---

def create_conversation(model_id, title="New Chat", system_prompt=None):
    cid = new_id()
    with get_db() as db:
        db.execute(
            "INSERT INTO conversations (id, title, model_id, system_prompt) VALUES (?,?,?,?)",
            (cid, title, model_id, system_prompt)
        )
    return get_conversation(cid)

def get_conversation(cid):
    with get_db() as db:
        row = db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None

def list_conversations(limit=50, offset=0):
    with get_db() as db:
        rows = db.execute("""
            SELECT c.*, COUNT(m.id) as message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return [dict(r) for r in rows]

def update_conversation(cid, **kwargs):
    allowed = {"title", "system_prompt", "model_id"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return get_conversation(cid)
    fields["updated_at"] = "datetime('now')"
    set_clause = ", ".join(
        f"{k}=datetime('now')" if k == "updated_at" else f"{k}=?"
        for k in fields
    )
    values = [v for k, v in fields.items() if k != "updated_at"]
    values.append(cid)
    with get_db() as db:
        db.execute(f"UPDATE conversations SET {set_clause} WHERE id=?", values)
    return get_conversation(cid)

def touch_conversation(cid):
    with get_db() as db:
        db.execute("UPDATE conversations SET updated_at=datetime('now') WHERE id=?", (cid,))

def delete_conversation(cid):
    with get_db() as db:
        db.execute("DELETE FROM conversations WHERE id=?", (cid,))

# --- Message helpers ---

def add_message(conversation_id, role, content=None, reasoning=None, tool_calls=None,
                tool_call_id=None, image_path=None):
    mid = new_id()
    with get_db() as db:
        db.execute("""
            INSERT INTO messages (id, conversation_id, role, content, reasoning, tool_calls, tool_call_id, image_path)
            VALUES (?,?,?,?,?,?,?,?)
        """, (mid, conversation_id, role, content, reasoning, tool_calls, tool_call_id, image_path))
    return mid

def get_messages(conversation_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at",
            (conversation_id,)
        ).fetchall()
        return [dict(r) for r in rows]

def _fts_query(raw):
    import re
    tokens = re.split(r'\s+', raw.strip())
    safe = []
    for t in tokens:
        # Strip FTS5 special chars; keep unicode word chars
        t = re.sub(r'[^\w\u0080-\uFFFF]', '', t, flags=re.UNICODE)
        if t:
            safe.append(f'{t}*')
    return ' '.join(safe) if safe else None

def search_messages(query, limit=20):
    fts_q = _fts_query(query)
    if not fts_q:
        return []
    with get_db() as db:
        # Use control-char markers (STX/ETX, 0x02/0x03) instead of literal
        # HTML so the frontend can HTML-escape user-authored content safely
        # and then substitute the markers for <mark>…</mark>.
        rows = db.execute("""
            SELECT
                m.conversation_id,
                c.title as conversation_title,
                c.system_prompt as conversation_system_prompt,
                m.id as message_id,
                m.role,
                snippet(messages_fts, 0, char(2), char(3), '...', 20) as snippet,
                m.created_at
            FROM messages_fts
            JOIN messages m ON m.rowid = messages_fts.rowid
            JOIN conversations c ON c.id = m.conversation_id
            WHERE messages_fts MATCH ?
            ORDER BY bm25(messages_fts), m.created_at DESC
            LIMIT ?
        """, (fts_q, limit)).fetchall()
        return [dict(r) for r in rows]
