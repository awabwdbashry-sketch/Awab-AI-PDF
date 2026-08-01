import os
import json
import sqlite3


# ---------------------------------------------------------------------------
# Permanent SQLite-backed chat storage — multi-user, with automatic
# backward-compatible migrations.
#
# Tables:
#   sessions            -> one row per browser session
#                          (session_id -> current conversation / theme)
#   folders             -> user-created folders ("📁 University", ...)
#   conversations       -> ONE conversation can now hold MULTIPLE PDFs.
#                          Has a title, pinned flag, and optional folder.
#   conversation_files   -> many-to-many: which PDFs belong to which
#                          conversation (this is what enables multi-PDF
#                          upload + cross-file search).
#   chat_messages       -> chat history, scoped by session_id +
#                          conversation_id (filename kept too, for legacy
#                          rows created before this migration).
#
# IMPORTANT: this module never drops or recreates the database. Every
# change here is either a new table (CREATE TABLE IF NOT EXISTS) or a new
# column (ALTER TABLE ... ADD COLUMN) added to an existing table, and old
# chat_messages rows (filename-only, no conversation_id) are automatically
# backfilled into their own conversation the first time init_db() runs
# against an old database. Nothing is ever deleted.
# ---------------------------------------------------------------------------

# DEPLOYMENT FIX: derive the DB path from this file's own location instead
# of a plain relative string ("app/database"), which only resolved
# correctly if the process's working directory happened to be the project
# root. This makes the database path correct no matter what directory the
# app is started from (local run, Docker, Railway/nixpacks, etc.).
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../app

DB_FOLDER = os.path.join(APP_DIR, "database")
DB_PATH = os.path.join(DB_FOLDER, "chat_history.db")


def get_connection():

    conn = sqlite3.connect(DB_PATH)

    # lets us access columns by name (row["role"]) instead of index
    conn.row_factory = sqlite3.Row

    # required so conversation deletes / message inserts inside the same
    # request stay consistent with foreign-key-ish cross-table cleanup
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


# ---------------------------------------------------------------------------
# Schema definitions used both to CREATE the tables the first time, and to
# figure out which columns are missing on an already-existing table.
#
# To add a new column in the future: just add one entry here (column name
# -> the SQL type/definition to use in an ALTER TABLE ... ADD COLUMN).
# init_db() will pick it up automatically and add it to any existing
# database the next time the app starts, without touching existing rows.
#
# Note: SQLite's ALTER TABLE ADD COLUMN does not allow NOT NULL unless a
# DEFAULT is also given, so any future required column needs a DEFAULT
# here too.
# ---------------------------------------------------------------------------
SESSIONS_COLUMNS = {
    "session_id":            "TEXT",
    "current_file":          "TEXT",
    "current_conversation_id": "INTEGER",
    "theme":                 "TEXT DEFAULT 'dark'",
    "created_at":            "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
}

FOLDERS_COLUMNS = {
    "id":         "INTEGER",
    "session_id": "TEXT",
    "name":       "TEXT",
    "collapsed":  "INTEGER DEFAULT 0",
    "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
}

CONVERSATIONS_COLUMNS = {
    "id":          "INTEGER",
    "session_id":  "TEXT",
    "title":       "TEXT",
    "pinned":      "INTEGER DEFAULT 0",
    "folder_id":   "INTEGER",
    "created_at":  "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "updated_at":  "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
}

CONVERSATION_FILES_COLUMNS = {
    "id":              "INTEGER",
    "conversation_id": "INTEGER",
    "filename":        "TEXT",
    "total_pages":     "INTEGER",
    "file_size":       "INTEGER",
    "uploaded_at":      "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
}

CHAT_MESSAGES_COLUMNS = {
    "session_id":       "TEXT",
    "conversation_id":  "INTEGER",
    "filename":         "TEXT",
    "role":             "TEXT",
    "text":             "TEXT",
    "pages":            "TEXT",
    "created_at":       "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
}


def table_exists(cursor, table_name):

    cursor.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name=?
        """,
        (table_name,)
    )

    return cursor.fetchone() is not None


def get_existing_columns(cursor, table_name):

    cursor.execute(
        f"PRAGMA table_info({table_name})"
    )

    return {row["name"] for row in cursor.fetchall()}


def migrate_table_columns(cursor, table_name, expected_columns):
    """
    Adds any column from expected_columns that is missing on the existing
    table via ALTER TABLE ... ADD COLUMN. Skips "id" (primary keys can't be
    added after the fact - that only matters for brand-new tables, which
    are created with the full schema by CREATE TABLE IF NOT EXISTS anyway).
    """

    if not table_exists(cursor, table_name):
        return

    existing_columns = get_existing_columns(cursor, table_name)

    for column_name, column_def in expected_columns.items():

        if column_name == "id":
            continue

        if column_name in existing_columns:
            continue

        print(
            f"🔧 Migration: adding missing column "
            f"'{column_name}' to '{table_name}'"
        )

        cursor.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"
        )


# ---------------------------------------------------------------------------
# One-time data migration (idempotent): every chat_messages row that
# predates the "conversations" table has a filename but no
# conversation_id. This creates one conversation per distinct
# (session_id, filename) pair still missing a conversation_id, links the
# file into conversation_files, and backfills conversation_id onto those
# messages. Rows that already have a conversation_id are left untouched,
# so this is safe to run on every startup.
# ---------------------------------------------------------------------------
def backfill_conversations(cursor):

    cursor.execute(
        """
        SELECT DISTINCT session_id, filename
        FROM chat_messages
        WHERE (conversation_id IS NULL OR conversation_id = '')
          AND session_id IS NOT NULL
          AND filename IS NOT NULL
        """
    )

    pairs = cursor.fetchall()

    for row in pairs:

        session_id = row["session_id"]
        filename = row["filename"]

        cursor.execute(
            """
            INSERT INTO conversations (session_id, title, pinned, folder_id)
            VALUES (?, ?, 0, NULL)
            """,
            (session_id, filename)
        )

        conversation_id = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO conversation_files (conversation_id, filename)
            VALUES (?, ?)
            """,
            (conversation_id, filename)
        )

        cursor.execute(
            """
            UPDATE chat_messages
            SET conversation_id = ?
            WHERE session_id = ? AND filename = ?
              AND (conversation_id IS NULL OR conversation_id = '')
            """,
            (conversation_id, session_id, filename)
        )

        print(
            f"🔧 Migration: backfilled conversation #{conversation_id} "
            f"for '{filename}' (session {session_id[:8]}...)"
        )


def init_db():

    os.makedirs(
        DB_FOLDER,
        exist_ok=True
    )

    conn = get_connection()

    cursor = conn.cursor()

    # --- create tables if this is a brand new database -------------------

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            current_file TEXT,
            current_conversation_id INTEGER,
            theme TEXT DEFAULT 'dark',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            name TEXT NOT NULL,
            collapsed INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            title TEXT,
            pinned INTEGER DEFAULT 0,
            folder_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            total_pages INTEGER,
            file_size INTEGER,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Chat messages, scoped by session_id AND (now) conversation_id.
    # filename is kept for legacy rows / single-file display convenience.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            conversation_id INTEGER,
            filename TEXT,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            pages TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    conn.commit()

    # --- migrate pre-existing tables to the current schema ---------------

    migrate_table_columns(cursor, "sessions", SESSIONS_COLUMNS)
    migrate_table_columns(cursor, "folders", FOLDERS_COLUMNS)
    migrate_table_columns(cursor, "conversations", CONVERSATIONS_COLUMNS)
    migrate_table_columns(cursor, "conversation_files", CONVERSATION_FILES_COLUMNS)
    migrate_table_columns(cursor, "chat_messages", CHAT_MESSAGES_COLUMNS)

    conn.commit()

    # --- backfill old filename-only chat_messages into real conversations
    backfill_conversations(cursor)

    conn.commit()

    conn.close()


# =============================================================================
# Sessions
# =============================================================================

def ensure_session(session_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR IGNORE INTO sessions (session_id, current_file)
        VALUES (?, NULL)
        """,
        (session_id,)
    )

    conn.commit()
    conn.close()


def set_current_file(session_id, filename):
    """Kept for backward compatibility with older call sites / templates."""

    ensure_session(session_id)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE sessions SET current_file = ? WHERE session_id = ?",
        (filename, session_id)
    )

    conn.commit()
    conn.close()


def get_current_file(session_id):

    if not session_id:
        return ""

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT current_file FROM sessions WHERE session_id = ?",
        (session_id,)
    )

    row = cursor.fetchone()
    conn.close()

    if row and row["current_file"]:
        return row["current_file"]

    return ""


def set_current_conversation(session_id, conversation_id):

    ensure_session(session_id)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE sessions SET current_conversation_id = ? WHERE session_id = ?",
        (conversation_id, session_id)
    )

    conn.commit()
    conn.close()


def get_current_conversation_id(session_id):

    if not session_id:
        return None

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT current_conversation_id FROM sessions WHERE session_id = ?",
        (session_id,)
    )

    row = cursor.fetchone()
    conn.close()

    if row and row["current_conversation_id"]:
        return row["current_conversation_id"]

    return None


def clear_current_file(session_id):

    ensure_session(session_id)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE sessions
        SET current_file = NULL, current_conversation_id = NULL
        WHERE session_id = ?
        """,
        (session_id,)
    )

    conn.commit()
    conn.close()


def set_theme(session_id, theme):

    ensure_session(session_id)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE sessions SET theme = ? WHERE session_id = ?",
        (theme, session_id)
    )

    conn.commit()
    conn.close()


def get_theme(session_id):

    if not session_id:
        return "dark"

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT theme FROM sessions WHERE session_id = ?",
        (session_id,)
    )

    row = cursor.fetchone()
    conn.close()

    if row and row["theme"]:
        return row["theme"]

    return "dark"


# =============================================================================
# Folders
# =============================================================================

def create_folder(session_id, name):

    name = (name or "").strip()

    if not name:
        return None

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO folders (session_id, name) VALUES (?, ?)",
        (session_id, name)
    )

    folder_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return folder_id


def rename_folder(session_id, folder_id, new_name):

    new_name = (new_name or "").strip()

    if not new_name:
        return

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE folders SET name = ? WHERE id = ? AND session_id = ?",
        (new_name, folder_id, session_id)
    )

    conn.commit()
    conn.close()


def delete_folder(session_id, folder_id):
    """
    Deletes the folder itself. Conversations that were inside it are NOT
    deleted - they're simply un-filed (folder_id -> NULL), same as
    dragging them back out of the folder first.
    """

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE conversations
        SET folder_id = NULL
        WHERE folder_id = ? AND session_id = ?
        """,
        (folder_id, session_id)
    )

    cursor.execute(
        "DELETE FROM folders WHERE id = ? AND session_id = ?",
        (folder_id, session_id)
    )

    conn.commit()
    conn.close()


def toggle_folder_collapsed(session_id, folder_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT collapsed FROM folders WHERE id = ? AND session_id = ?",
        (folder_id, session_id)
    )

    row = cursor.fetchone()

    if row:

        new_value = 0 if row["collapsed"] else 1

        cursor.execute(
            "UPDATE folders SET collapsed = ? WHERE id = ? AND session_id = ?",
            (new_value, folder_id, session_id)
        )

        conn.commit()

    conn.close()


def get_folders(session_id):

    if not session_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, collapsed
        FROM folders
        WHERE session_id = ?
        ORDER BY name COLLATE NOCASE ASC
        """,
        (session_id,)
    )

    rows = cursor.fetchall()
    conn.close()

    return [
        {"id": r["id"], "name": r["name"], "collapsed": bool(r["collapsed"])}
        for r in rows
    ]


def move_conversation_to_folder(session_id, conversation_id, folder_id):
    """folder_id can be None to un-file the conversation."""

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE conversations
        SET folder_id = ?
        WHERE id = ? AND session_id = ?
        """,
        (folder_id, conversation_id, session_id)
    )

    conn.commit()
    conn.close()


# =============================================================================
# Conversations (a conversation can hold multiple PDFs)
# =============================================================================

def create_conversation(session_id, title, folder_id=None):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO conversations (session_id, title, pinned, folder_id)
        VALUES (?, ?, 0, ?)
        """,
        (session_id, title, folder_id)
    )

    conversation_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return conversation_id


def touch_conversation(conversation_id):
    """Bumps updated_at - called whenever a message is added, so the
    sidebar's "most recent activity first" ordering stays accurate."""

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (conversation_id,)
    )

    conn.commit()
    conn.close()


def rename_conversation(session_id, conversation_id, new_title):

    new_title = (new_title or "").strip()

    if not new_title:
        return

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE conversations
        SET title = ?
        WHERE id = ? AND session_id = ?
        """,
        (new_title, conversation_id, session_id)
    )

    conn.commit()
    conn.close()


def set_conversation_title_if_default(conversation_id, generated_title):
    """
    Used by the auto-title feature: only overwrites the title if the user
    hasn't already renamed it away from the placeholder we gave it at
    creation time (which is always the first uploaded filename). This
    keeps a manual rename from ever being silently clobbered by the AI
    title generator running later.
    """

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT title FROM conversations WHERE id = ?",
        (conversation_id,)
    )

    row = cursor.fetchone()

    if not row:
        conn.close()
        return

    cursor.execute(
        """
        SELECT filename FROM conversation_files
        WHERE conversation_id = ?
        ORDER BY id ASC LIMIT 1
        """,
        (conversation_id,)
    )

    file_row = cursor.fetchone()
    original_placeholder = file_row["filename"] if file_row else None

    if row["title"] == original_placeholder:

        cursor.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (generated_title, conversation_id)
        )

        conn.commit()

    conn.close()


def pin_conversation(session_id, conversation_id, pinned=True):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE conversations
        SET pinned = ?
        WHERE id = ? AND session_id = ?
        """,
        (1 if pinned else 0, conversation_id, session_id)
    )

    conn.commit()
    conn.close()


def add_file_to_conversation(conversation_id, filename, total_pages=None, file_size=None):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO conversation_files (conversation_id, filename, total_pages, file_size)
        VALUES (?, ?, ?, ?)
        """,
        (conversation_id, filename, total_pages, file_size)
    )

    conn.commit()
    conn.close()


def get_conversation_files(conversation_id):

    if not conversation_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT filename, total_pages, file_size, uploaded_at
        FROM conversation_files
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,)
    )

    rows = cursor.fetchall()
    conn.close()

    return [dict(r) for r in rows]


def get_conversation(session_id, conversation_id):

    if not conversation_id:
        return None

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, title, pinned, folder_id, created_at, updated_at
        FROM conversations
        WHERE id = ? AND session_id = ?
        """,
        (conversation_id, session_id)
    )

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    conversation = dict(row)
    conversation["files"] = get_conversation_files(conversation_id)

    return conversation


def get_conversation_by_filename(session_id, filename):
    """
    Finds the (most recently touched) conversation that contains this
    filename for this session. Used by /open-chat, which historically
    only knew a filename, to resolve it back to a conversation_id.
    """

    if not session_id or not filename:
        return None

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT c.id
        FROM conversations c
        JOIN conversation_files cf ON cf.conversation_id = c.id
        WHERE c.session_id = ? AND cf.filename = ?
        ORDER BY c.updated_at DESC
        LIMIT 1
        """,
        (session_id, filename)
    )

    row = cursor.fetchone()
    conn.close()

    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Sidebar listing. Returns every conversation for this session, each with
# its files, ready for the template to render pinned-first, then grouped
# by folder. Session isolation is preserved exactly as before - this only
# ever looks at rows matching this specific session_id.
# ---------------------------------------------------------------------------
def get_conversations(session_id):

    if not session_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, title, pinned, folder_id, updated_at
        FROM conversations
        WHERE session_id = ?
        ORDER BY pinned DESC, updated_at DESC
        """,
        (session_id,)
    )

    rows = cursor.fetchall()

    conversations = []

    for row in rows:

        cursor.execute(
            """
            SELECT filename FROM conversation_files
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (row["id"],)
        )

        files = [f["filename"] for f in cursor.fetchall()]

        # a conversation with zero files (shouldn't normally happen) is
        # skipped from the sidebar rather than shown as a dead entry
        if not files:
            continue

        conversations.append({
            "id": row["id"],
            "title": row["title"] or files[0],
            "pinned": bool(row["pinned"]),
            "folder_id": row["folder_id"],
            "files": files,
            "file_count": len(files),
            "last_activity": row["updated_at"],
        })

    conn.close()

    return conversations


def delete_conversation(session_id, conversation_id):
    """
    Deletes the conversation, its chat_messages, and its
    conversation_files rows (all scoped strictly to session_id, so this
    can never touch another session's data). The uploaded PDFs themselves
    and their extracted-text / vector-store data on disk are intentionally
    left untouched.
    """

    if not session_id or not conversation_id:
        return

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM chat_messages WHERE session_id = ? AND conversation_id = ?",
        (session_id, conversation_id)
    )

    cursor.execute(
        "DELETE FROM conversation_files WHERE conversation_id = ? "
        "AND conversation_id IN (SELECT id FROM conversations WHERE session_id = ?)",
        (conversation_id, session_id)
    )

    cursor.execute(
        "DELETE FROM conversations WHERE id = ? AND session_id = ?",
        (conversation_id, session_id)
    )

    cursor.execute(
        """
        UPDATE sessions
        SET current_file = NULL, current_conversation_id = NULL
        WHERE session_id = ? AND current_conversation_id = ?
        """,
        (session_id, conversation_id)
    )

    conn.commit()
    conn.close()


def delete_conversations_bulk(session_id, conversation_ids):

    for conversation_id in conversation_ids:
        delete_conversation(session_id, conversation_id)


# =============================================================================
# Chat messages
# =============================================================================

def save_message(session_id, conversation_id, filename, role, text, pages=None):
    """
    pages: for AI messages, a list of source references. Each entry can
    be either an int (legacy, single-file) or a dict
    {"filename": "...", "page": N} (multi-file). Stored as a JSON string.
    """

    conn = get_connection()
    cursor = conn.cursor()

    pages_json = json.dumps(pages) if pages else None

    cursor.execute(
        """
        INSERT INTO chat_messages (session_id, conversation_id, filename, role, text, pages)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, conversation_id, filename, role, text, pages_json)
    )

    conn.commit()
    conn.close()

    if conversation_id:
        touch_conversation(conversation_id)


def get_chat_history(session_id, conversation_id):

    if not session_id or not conversation_id:
        return []

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT role, text, pages, filename
        FROM chat_messages
        WHERE session_id = ? AND conversation_id = ?
        ORDER BY id ASC
        """,
        (session_id, conversation_id)
    )

    rows = cursor.fetchall()
    conn.close()

    messages = []

    for row in rows:

        message = {
            "role": row["role"],
            "text": row["text"]
        }

        if row["pages"]:

            raw_pages = json.loads(row["pages"])

            # Normalize legacy int-only page lists (single-file era) into
            # the {"filename":..., "page":...} shape the new source-card
            # UI expects, using the message's own filename column.
            normalized = []

            for entry in raw_pages:

                if isinstance(entry, dict):
                    normalized.append(entry)
                else:
                    normalized.append({
                        "filename": row["filename"],
                        "page": entry
                    })

            message["sources"] = normalized

        messages.append(message)

    return messages


def count_user_messages(session_id, conversation_id):
    """Used to decide whether this is the FIRST user message in a
    conversation (auto-title trigger)."""

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*) AS c FROM chat_messages
        WHERE session_id = ? AND conversation_id = ? AND role = 'user'
        """,
        (session_id, conversation_id)
    )

    row = cursor.fetchone()
    conn.close()

    return row["c"] if row else 0


# ---------------------------------------------------------------------------
# Full-text-ish search across this session's conversations: matches on
# title, any filename in the conversation, or folder name. Case
# insensitive substring match, done in Python (dataset is small - one
# session's own conversations - so no need for SQLite FTS).
# ---------------------------------------------------------------------------
def search_conversations(session_id, query):

    query = (query or "").strip().lower()

    conversations = get_conversations(session_id)

    if not query:
        return conversations

    folders_by_id = {f["id"]: f["name"] for f in get_folders(session_id)}

    results = []

    for conv in conversations:

        haystack = " ".join([
            conv["title"] or "",
            " ".join(conv["files"]),
            folders_by_id.get(conv["folder_id"], "")
        ]).lower()

        if query in haystack:
            results.append(conv)

    return results


# Auto-create/migrate the database and tables as soon as this module is
# imported, so main.py doesn't need any extra setup step.
init_db()
