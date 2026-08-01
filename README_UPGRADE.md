# Awab AI — Upgrade Notes

This is your app with all 21 requested features added on top of the existing
architecture (FastAPI + Jinja2 + SQLite + FAISS + Gemini). Nothing that
worked before was removed — old databases upgrade automatically the first
time you run the new code (see "Database migration" below).

## What changed, file by file

- **`app/database/chat_database.py`** — biggest change. Added `folders`,
  `conversations`, and `conversation_files` tables so one conversation can
  now hold multiple PDFs, live in a folder, be pinned, and have a custom
  title. Old `chat_messages` rows (filename-only, no conversation) are
  automatically migrated into their own conversation the first time the
  app starts against an old `chat_history.db` — nothing is deleted.
- **`app/ai/gemini.py`** — `ask_gemini()` is untouched. Added
  `ask_gemini_stream()` (real token streaming, generator) and
  `generate_conversation_title()` (auto-title feature).
- **`app/services/search_engine.py`** — `search_in_text()` is untouched
  (kept for backward compatibility). Added `gather_context()` (shared
  retrieval step) and `search_in_files()` for multi-PDF search: it
  searches every uploaded PDF's FAISS index, merges the best matches
  across files, and returns sources tagged with which file/page they
  came from.
- **`app/services/vector_store.py`** — `create_vector_store()` is
  byte-for-byte the function you had. Added one small helper,
  `vector_store_exists()`, used to skip re-embedding a PDF that's
  already been processed (duplicate-upload protection).
- **`app/services/pdf_reader.py`** — unchanged.
- **`main.py`** — rewritten routing layer: multi-file upload (JSON for
  the drag-and-drop uploader, redirect fallback for non-JS), a new
  `/chat-stream` SSE endpoint for real streaming, plus new endpoints for
  folders, pinning, renaming, moving, bulk delete, search, theme, and
  export. `/chat` (the old non-streaming endpoint) still works exactly
  as a fallback.
- **`app/templates/chat.html`** — full front-end rewrite: dark/light
  theme, resizable/collapsible sidebar with mobile drawer, folders,
  pinned conversations, drag & drop upload with a progress bar, markdown
  + code-highlighting + copy button in AI bubbles, source cards instead
  of plain links, multi-select delete, keyboard shortcuts, and the real
  streaming client.
- **`app/static/css/style.css`** — left exactly as uploaded. It isn't
  actually used by `chat.html` (which has always had its own inline
  `<style>` block) — I kept the link tag so nothing that depended on it
  elsewhere breaks.

## Database migration

Nothing to do manually. `init_db()` runs on import and:
1. Adds any missing columns to existing tables (`ALTER TABLE ... ADD COLUMN`).
2. Creates the new `folders`, `conversations`, `conversation_files` tables.
3. Backfills every old filename-only `chat_messages` row into its own
   conversation (titled after the original filename), so your existing
   chat history shows up in the new sidebar immediately.

This was tested against a simulated pre-upgrade database (old schema,
real rows) and confirmed lossless.

## One thing I changed on purpose, flagged up front

**Real streaming (feature 1)** needed a second way to talk to the server:
Server-Sent Events over `fetch`, instead of the old submit → redirect →
reload cycle. I added this as a *new* endpoint (`/chat-stream`) and left
`/chat` working exactly as before, so nothing that depended on the old
behavior breaks — but this is a genuine addition to the request/response
architecture, not just styling, so I want you to know it's there rather
than have you find it by reading a diff.

## New endpoints

| Route | Purpose |
|---|---|
| `POST /upload-chat` | multi-file upload, JSON or redirect depending on `Accept` header |
| `POST /chat-stream` | SSE streaming chat (feature 1) |
| `POST /chat` | non-streaming fallback (unchanged behavior) |
| `GET /open-chat?conversation_id=` | switch conversations |
| `POST /new-chat`, `/delete-chat`, `/delete-chats-bulk` | |
| `POST /rename-chat`, `/pin-chat`, `/move-chat` | |
| `POST /create-folder`, `/rename-folder`, `/delete-folder`, `/toggle-folder` | |
| `POST /set-theme` | dark/light persistence |
| `GET /search-chats?q=` | server-side search (client-side filter is also wired up for instant results) |
| `GET /export-chat?conversation_id=&format=md\|txt\|json\|pdf` | |

## Known limitation: PDF export + Arabic

`format=pdf` uses PyMuPDF's `insert_htmlbox`, which gives good Arabic
shaping on recent PyMuPDF versions. If your installed PyMuPDF is older
and doesn't have that method, it automatically falls back to a plain-text
PDF (still downloads correctly, just without rich formatting). Markdown,
TXT, and JSON export don't have this limitation — they're exact.

## Setup

```bash
pip install -r requirements.txt
# .env with GOOGLE_API_KEY=... (same as before)
uvicorn main:app --reload
```

No config changes needed beyond what you already had.
