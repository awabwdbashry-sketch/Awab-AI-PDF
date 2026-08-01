from fastapi import FastAPI, Request, UploadFile, Form
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse, StreamingResponse, FileResponse

from app.services.pdf_reader import extract_text, save_pages_json
from app.services.search_engine import search_in_files, gather_context
from app.services.vector_store import create_vector_store, vector_store_exists
from app.ai.gemini import ask_gemini_stream, generate_conversation_title

from app.database.chat_database import (
    save_message,
    get_chat_history,
    set_current_file,
    get_current_file,
    get_conversations,
    delete_conversation,
    delete_conversations_bulk,
    clear_current_file,
    set_current_conversation,
    get_current_conversation_id,
    get_conversation,
    get_conversation_by_filename,
    get_conversation_files,
    create_conversation,
    add_file_to_conversation,
    rename_conversation,
    pin_conversation,
    set_conversation_title_if_default,
    count_user_messages,
    create_folder,
    rename_folder,
    delete_folder,
    toggle_folder_collapsed,
    get_folders,
    move_conversation_to_folder,
    search_conversations,
    set_theme,
    get_theme,
)

import os
import re
import json
import uuid
from datetime import datetime



app = FastAPI()



# ---------------------------------------------------------------------------
# DEPLOYMENT FIX: all folders below are now built from this file's own
# location (BASE_DIR = the "app" directory) instead of plain relative
# strings like "app/static". Relative strings only resolve correctly if the
# process is started with its working directory set to the project root -
# that's not guaranteed on every platform/start command, and was the root
# cause of static/template/upload files not being found in some deployment
# setups. Using __file__ makes this correct regardless of where the process
# is launched from (local run, Docker, Railway, etc.).
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # .../app

STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
TEXT_FOLDER = os.path.join(BASE_DIR, "uploads", "texts")
EXPORT_FOLDER = os.path.join(BASE_DIR, "uploads", "exports")


os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TEXT_FOLDER, exist_ok=True)
os.makedirs(EXPORT_FOLDER, exist_ok=True)


app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static"
)


app.mount(
    "/uploads",
    StaticFiles(directory=UPLOAD_FOLDER),
    name="uploads"
)



templates = Jinja2Templates(
    directory=TEMPLATES_DIR
)



UPLOAD_TIME_FORMAT = "%d %b %Y • %I:%M %p"



SESSION_COOKIE_NAME = "session_id"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days



def get_or_create_session_id(request: Request) -> str:

    session_id = request.cookies.get(SESSION_COOKIE_NAME)

    if not session_id:
        session_id = str(uuid.uuid4())

    return session_id



def attach_session_cookie(response, session_id: str):

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax"
    )

    return response



# ---------------------------------------------------------------------------
# True whenever the caller is our own JS (drag&drop uploader, fetch/XHR
# calls for the sidebar actions, the streaming chat, etc.) rather than a
# plain browser form submit / direct link. Those AJAX callers get JSON
# back; anything else gets the classic PRG redirect, so the app still
# works with JS disabled.
# ---------------------------------------------------------------------------
def wants_json(request: Request) -> bool:

    accept = request.headers.get("accept", "")

    requested_with = request.headers.get("x-requested-with", "")

    return "application/json" in accept or requested_with == "XMLHttpRequest"




SAFE_FILENAME_RE = re.compile(r'[^A-Za-z0-9\u0600-\u06FF _.\-\(\)]+')



def clean_filename(filename):
    """
    UNCHANGED core behavior from before, PLUS stripped of characters that
    aren't safe for a filesystem path (security requirement: safe
    filenames / no path traversal). Still purely a string transform -
    doesn't touch the actual upload/storage logic.
    """

    filename = os.path.basename(filename).strip()

    filename = filename.replace("..", ".")

    filename = filename.replace(
        " .pdf",
        ".pdf"
    )

    filename = filename.replace(
        ". .pdf",
        ".pdf"
    )

    filename = SAFE_FILENAME_RE.sub("", filename)

    filename = " ".join(filename.split())

    return filename or f"file_{uuid.uuid4().hex[:8]}.pdf"



PDF_MAGIC = b"%PDF"



def looks_like_pdf(raw_bytes: bytes) -> bool:
    """Cheap content sniff - guards against a non-PDF renamed to .pdf."""

    return raw_bytes[:4] == PDF_MAGIC



def get_file_metadata_from_disk(filename):
    """Reads total_pages back from the saved {filename}.json (same data
    extract_text() produced at upload time) and the file's mtime as its
    upload time. Returns ("-", "-") for anything that can't be
    determined."""

    if not filename:
        return "-", "-"

    total_pages = "-"

    json_path = os.path.join(
        TEXT_FOLDER,
        filename.replace(".pdf", ".json")
    )

    if os.path.exists(json_path):

        try:

            with open(json_path, "r", encoding="utf-8") as f:
                pages = json.load(f)

            total_pages = len(pages)

        except Exception:

            total_pages = "-"

    upload_time = "-"

    file_path = os.path.join(UPLOAD_FOLDER, filename)

    if os.path.exists(file_path):

        try:

            mtime = os.path.getmtime(file_path)

            upload_time = datetime.fromtimestamp(mtime).strftime(
                UPLOAD_TIME_FORMAT
            )

        except Exception:

            upload_time = "-"

    return total_pages, upload_time



def format_conversation_time(raw_timestamp):

    if not raw_timestamp:
        return "-"

    try:

        dt = datetime.strptime(raw_timestamp, "%Y-%m-%d %H:%M:%S")

        return dt.strftime(UPLOAD_TIME_FORMAT)

    except Exception:

        return raw_timestamp



def format_file_size(num_bytes):

    if not num_bytes:
        return "-"

    for unit in ["B", "KB", "MB", "GB"]:

        if num_bytes < 1024:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"

        num_bytes /= 1024

    return f"{num_bytes:.1f} TB"



# ---------------------------------------------------------------------------
# Builds the full sidebar payload: folders (with their conversations
# nested inside) + an "unfiled" list of conversations with no folder,
# pinned conversations always sorted first within each group.
# ---------------------------------------------------------------------------
def get_sidebar_data(session_id, search_query=None):

    conversations = (
        search_conversations(session_id, search_query)
        if search_query else
        get_conversations(session_id)
    )

    for conv in conversations:
        conv["last_activity_display"] = format_conversation_time(conv["last_activity"])

    folders = get_folders(session_id)

    folder_map = {f["id"]: {**f, "conversations": []} for f in folders}

    unfiled = []

    for conv in conversations:

        if conv["folder_id"] and conv["folder_id"] in folder_map:
            folder_map[conv["folder_id"]]["conversations"].append(conv)
        else:
            unfiled.append(conv)

    return {
        "folders": list(folder_map.values()),
        "unfiled": unfiled,
    }



def get_current_conversation_context(session_id):
    """Returns (conversation_id, conversation_dict_or_None). Note:
    conversation["files"] is a list of DICTS (filename/total_pages/
    file_size/uploaded_at) - use conversation_filenames() below to get
    plain filename strings for search/messages."""

    conversation_id = get_current_conversation_id(session_id)

    if not conversation_id:
        return None, None

    conversation = get_conversation(session_id, conversation_id)

    return conversation_id, conversation



def conversation_filenames(conversation):

    if not conversation or not conversation.get("files"):
        return []

    return [f["filename"] for f in conversation["files"]]




# ---------------------------------------------------------------------------
# Single entry point of the app - the chat page itself.
# ---------------------------------------------------------------------------
@app.get("/")
def home(request: Request):

    session_id = get_or_create_session_id(request)

    conversation_id, conversation = get_current_conversation_context(session_id)

    messages = get_chat_history(session_id, conversation_id) if conversation_id else []

    files = []

    if conversation:

        for f in conversation["files"]:

            files.append({
                "filename": f["filename"],
                "total_pages": f["total_pages"] if f["total_pages"] else "-",
                "file_size": format_file_size(f["file_size"]),
                "uploaded_at": format_conversation_time(f["uploaded_at"]),
            })

    theme = get_theme(session_id)

    sidebar = get_sidebar_data(session_id)

    response = templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "conversation_id": conversation_id,
            "conversation_title": conversation["title"] if conversation else None,
            "conversation_pinned": conversation["pinned"] if conversation else False,
            "files": files,
            "messages": messages,
            "theme": theme,
            "folders": sidebar["folders"],
            "unfiled_conversations": sidebar["unfiled"],
        }
    )

    attach_session_cookie(response, session_id)

    return response



# ---------------------------------------------------------------------------
# Upload endpoint - supports:
#   - one or many PDFs in the same request (field name "files")
#   - uploading into a brand new conversation (no conversation_id sent)
#   - adding more PDFs into the CURRENTLY open conversation
#     (conversation_id sent, or falls back to the session's current one)
#   - JSON response for the drag&drop / progress-bar uploader, or a
#     classic redirect for a plain form submit (no JS)
# ---------------------------------------------------------------------------
@app.post("/upload-chat")
async def upload_chat_pdf(request: Request):

    session_id = get_or_create_session_id(request)

    form = await request.form()

    raw_files = form.getlist("files") or []

    single = form.get("file")
    if single is not None:
        raw_files.append(single)

    upload_targets = [f for f in raw_files if hasattr(f, "filename") and f.filename]

    conversation_id_raw = form.get("conversation_id")
    conversation_id = int(conversation_id_raw) if conversation_id_raw else get_current_conversation_id(session_id)

    if not upload_targets:

        if wants_json(request):
            return JSONResponse({"success": False, "error": "لم يتم اختيار أي ملف"}, status_code=400)

        response = RedirectResponse(url="/", status_code=303)
        attach_session_cookie(response, session_id)
        return response

    saved_files = []
    rejected_files = []

    conversation = get_conversation(session_id, conversation_id) if conversation_id else None
    existing_filenames = set(conversation_filenames(conversation)) if conversation else set()

    for upload in upload_targets:

        filename = clean_filename(upload.filename)

        if not filename.lower().endswith(".pdf"):
            rejected_files.append({"filename": upload.filename, "reason": "ليس ملف PDF"})
            continue

        raw_bytes = await upload.read()

        if not looks_like_pdf(raw_bytes):
            rejected_files.append({"filename": filename, "reason": "الملف تالف أو ليس PDF حقيقي"})
            continue

        file_path = os.path.join(UPLOAD_FOLDER, filename)

        already_has_text = vector_store_exists(filename)

        with open(file_path, "wb") as buffer:
            buffer.write(raw_bytes)

        if already_has_text:

            # Same content already processed before (matched by filename
            # hash) - reuse the existing vector store instead of paying
            # for SentenceTransformer + FAISS again.
            print(f"♻️ تخطي إعادة المعالجة - يوجد Vector Store بالفعل: {filename}")

            json_path = os.path.join(TEXT_FOLDER, filename.replace(".pdf", ".json"))

            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    total_pages = len(json.load(f))
            else:
                total_pages = "-"

        else:

            pages = extract_text(file_path)

            total_pages = len(pages)

            json_path = os.path.join(TEXT_FOLDER, filename.replace(".pdf", ".json"))

            save_pages_json(pages, json_path)

            create_vector_store(filename)

        saved_files.append({
            "filename": filename,
            "total_pages": total_pages,
            "file_size": len(raw_bytes),
        })

    if not saved_files:

        if wants_json(request):
            return JSONResponse({"success": False, "error": "لم يتم قبول أي ملف", "rejected": rejected_files}, status_code=400)

        response = RedirectResponse(url="/", status_code=303)
        attach_session_cookie(response, session_id)
        return response

    is_new_conversation = conversation is None

    if is_new_conversation:

        conversation_id = create_conversation(
            session_id,
            title=saved_files[0]["filename"],
        )

    added_filenames = []

    for f in saved_files:

        if f["filename"] in existing_filenames:
            continue  # duplicate upload requirement: don't add the same file twice

        add_file_to_conversation(
            conversation_id,
            f["filename"],
            total_pages=f["total_pages"] if isinstance(f["total_pages"], int) else None,
            file_size=f["file_size"],
        )

        added_filenames.append(f["filename"])
        existing_filenames.add(f["filename"])

    set_current_conversation(session_id, conversation_id)
    set_current_file(session_id, saved_files[0]["filename"])  # legacy compat field

    if added_filenames:

        if len(added_filenames) == 1:
            confirm_text = f"✅ تم رفع الملف \"{added_filenames[0]}\" بنجاح، يمكنك الآن طرح أي سؤال عنه."
        else:
            file_list = "، ".join(added_filenames)
            confirm_text = f"✅ تم رفع {len(added_filenames)} ملفات بنجاح ({file_list})، يمكنك الآن طرح أي سؤال عنها جميعًا."

        save_message(session_id, conversation_id, added_filenames[0], "ai", confirm_text)

    if wants_json(request):

        return JSONResponse({
            "success": True,
            "conversation_id": conversation_id,
            "added_files": added_filenames,
            "rejected": rejected_files,
        })

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



# ---------------------------------------------------------------------------
# Sidebar: clicking a conversation.
# ---------------------------------------------------------------------------
@app.get("/open-chat")
def open_chat(request: Request, conversation_id: int = None, file: str = None):

    session_id = get_or_create_session_id(request)

    resolved_id = conversation_id

    if not resolved_id and file:
        # backward compat with any old links built around ?file=
        resolved_id = get_conversation_by_filename(session_id, file)

    if resolved_id:

        conversation = get_conversation(session_id, resolved_id)

        if conversation:

            filenames = conversation_filenames(conversation)

            set_current_conversation(session_id, resolved_id)
            set_current_file(session_id, filenames[0] if filenames else None)

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



@app.post("/new-chat")
def new_chat(request: Request):

    session_id = get_or_create_session_id(request)

    clear_current_file(session_id)

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



@app.post("/delete-chat")
def delete_chat(request: Request, conversation_id: int = Form(...)):

    session_id = get_or_create_session_id(request)

    delete_conversation(session_id, conversation_id)

    if wants_json(request):
        return JSONResponse({"success": True})

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



# Feature 17: multi-select delete. Accepts a comma-separated list of ids.
@app.post("/delete-chats-bulk")
def delete_chats_bulk(request: Request, conversation_ids: str = Form(...)):

    session_id = get_or_create_session_id(request)

    ids = [int(x) for x in conversation_ids.split(",") if x.strip().isdigit()]

    delete_conversations_bulk(session_id, ids)

    if wants_json(request):
        return JSONResponse({"success": True, "deleted": ids})

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



# Feature 15: rename conversation.
@app.post("/rename-chat")
def rename_chat(request: Request, conversation_id: int = Form(...), title: str = Form(...)):

    session_id = get_or_create_session_id(request)

    rename_conversation(session_id, conversation_id, title)

    if wants_json(request):
        return JSONResponse({"success": True, "title": title.strip()})

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



# Feature 3: pin / unpin.
@app.post("/pin-chat")
def pin_chat(request: Request, conversation_id: int = Form(...), pinned: str = Form(...)):

    session_id = get_or_create_session_id(request)

    pin_conversation(session_id, conversation_id, pinned == "true")

    if wants_json(request):
        return JSONResponse({"success": True})

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



# ---------------------------- Folders (feature 4) --------------------------

@app.post("/create-folder")
def create_folder_route(request: Request, name: str = Form(...)):

    session_id = get_or_create_session_id(request)

    folder_id = create_folder(session_id, name)

    if wants_json(request):
        return JSONResponse({"success": True, "folder_id": folder_id})

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



@app.post("/rename-folder")
def rename_folder_route(request: Request, folder_id: int = Form(...), name: str = Form(...)):

    session_id = get_or_create_session_id(request)

    rename_folder(session_id, folder_id, name)

    if wants_json(request):
        return JSONResponse({"success": True})

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



@app.post("/delete-folder")
def delete_folder_route(request: Request, folder_id: int = Form(...)):

    session_id = get_or_create_session_id(request)

    delete_folder(session_id, folder_id)

    if wants_json(request):
        return JSONResponse({"success": True})

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



@app.post("/toggle-folder")
def toggle_folder_route(request: Request, folder_id: int = Form(...)):

    session_id = get_or_create_session_id(request)

    toggle_folder_collapsed(session_id, folder_id)

    if wants_json(request):
        return JSONResponse({"success": True})

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



@app.post("/move-chat")
def move_chat_route(request: Request, conversation_id: int = Form(...), folder_id: str = Form("")):

    session_id = get_or_create_session_id(request)

    target_folder_id = int(folder_id) if folder_id.strip().isdigit() else None

    move_conversation_to_folder(session_id, conversation_id, target_folder_id)

    if wants_json(request):
        return JSONResponse({"success": True})

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



# ------------------------------- Theme (feature 10) -------------------------

@app.post("/set-theme")
async def set_theme_route(request: Request):

    session_id = get_or_create_session_id(request)

    body = await request.json()

    theme = body.get("theme", "dark")

    if theme not in ("dark", "light"):
        theme = "dark"

    set_theme(session_id, theme)

    return JSONResponse({"success": True, "theme": theme})



# ------------------------------- Search (feature 14) -------------------------

@app.get("/search-chats")
def search_chats_route(request: Request, q: str = ""):

    session_id = get_or_create_session_id(request)

    sidebar = get_sidebar_data(session_id, search_query=q)

    return JSONResponse(sidebar)



# ----------------------------- Non-streaming chat ---------------------------
# Kept as a working fallback for JS-disabled browsers / any client that
# doesn't use the SSE endpoint below. Same PRG pattern as before.
# ---------------------------------------------------------------------------
@app.post("/chat")
async def chat_question(request: Request, question: str = Form(...)):

    session_id = get_or_create_session_id(request)

    conversation_id, conversation = get_current_conversation_context(session_id)

    filenames = conversation_filenames(conversation)

    if not conversation_id or not filenames:

        response = templates.TemplateResponse(
            "chat.html",
            {
                "request": request,
                "conversation_id": None,
                "conversation_title": None,
                "conversation_pinned": False,
                "files": [],
                "messages": [{"role": "ai", "text": "📎 قم برفع ملف PDF أولاً"}],
                "theme": get_theme(session_id),
                **get_sidebar_data(session_id),
            }
        )

        attach_session_cookie(response, session_id)
        return response

    is_first_message = count_user_messages(session_id, conversation_id) == 0

    result = search_in_files(filenames, question)

    save_message(session_id, conversation_id, filenames[0], "user", question)
    save_message(session_id, conversation_id, filenames[0], "ai", result["answer"], result["sources"])

    if is_first_message:

        title = generate_conversation_title(question, result["answer"])
        set_conversation_title_if_default(conversation_id, title)

    response = RedirectResponse(url="/", status_code=303)
    attach_session_cookie(response, session_id)
    return response



# -------------------------- Real streaming chat (feature 1) -----------------
# Server-Sent Events endpoint. The user's question is saved immediately;
# tokens are streamed to the browser as Gemini produces them; the full
# answer is saved once streaming completes. Nothing here touches the PDF
# search logic itself (gather_context / ask_gemini_stream do the same
# retrieval search_in_files() does, just split so we can stream).
# ---------------------------------------------------------------------------
@app.post("/chat-stream")
async def chat_stream(request: Request, question: str = Form(...)):

    session_id = get_or_create_session_id(request)

    conversation_id, conversation = get_current_conversation_context(session_id)

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    files = conversation_filenames(conversation)

    if not conversation_id or not files:

        async def empty_stream():
            yield sse({"type": "error", "text": "📎 قم برفع ملف PDF أولاً"})

        return StreamingResponse(empty_stream(), media_type="text/event-stream")

    is_first_message = count_user_messages(session_id, conversation_id) == 0

    save_message(session_id, conversation_id, files[0], "user", question)

    def event_stream():

        context, sources = gather_context(files, question)

        if context is None:
            error_text = "لم يتم العثور على قاعدة البيانات الخاصة بالملفات."
            save_message(session_id, conversation_id, files[0], "ai", error_text, [])
            yield sse({"type": "error", "text": error_text})
            return

        if not context.strip():
            error_text = "لم يتم العثور على نص مناسب داخل الملفات."
            save_message(session_id, conversation_id, files[0], "ai", error_text, [])
            yield sse({"type": "error", "text": error_text})
            return

        yield sse({"type": "sources", "sources": sources})

        full_answer = ""

        try:

            for token in ask_gemini_stream(context, question):

                full_answer += token

                yield sse({"type": "token", "text": token})

        except Exception as e:

            full_answer = full_answer or f"حدث خطأ: {e}"

            yield sse({"type": "error", "text": str(e)})

        save_message(session_id, conversation_id, files[0], "ai", full_answer, sources)

        if is_first_message:

            title = generate_conversation_title(question, full_answer)
            set_conversation_title_if_default(conversation_id, title)

            yield sse({"type": "title", "title": title})

        yield sse({"type": "done"})

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    attach_session_cookie(response, session_id)
    return response



# ------------------------------ Export (feature 16) --------------------------

def _export_filename(conversation, extension):

    base = (conversation["title"] or "chat").strip()
    base = SAFE_FILENAME_RE.sub("", base) or "chat"

    return f"{base}.{extension}"



def _render_markdown_export(conversation, messages):

    lines = [f"# {conversation['title']}", ""]

    lines.append("**الملفات:** " + "، ".join(conversation_filenames(conversation)))
    lines.append("")

    for msg in messages:

        speaker = "👤 المستخدم" if msg["role"] == "user" else "🤖 Awab AI"

        lines.append(f"### {speaker}")
        lines.append("")
        lines.append(msg["text"])
        lines.append("")

        for src in msg.get("sources", []):
            lines.append(f"> 📎 {src.get('filename')} — صفحة {src.get('page')}")

        lines.append("")

    return "\n".join(lines)



def _render_txt_export(conversation, messages):

    lines = [conversation["title"], "=" * len(conversation["title"]), ""]

    for msg in messages:

        speaker = "المستخدم" if msg["role"] == "user" else "Awab AI"

        lines.append(f"{speaker}: {msg['text']}")

        for src in msg.get("sources", []):
            lines.append(f"   [مصدر: {src.get('filename')} - صفحة {src.get('page')}]")

        lines.append("")

    return "\n".join(lines)



@app.get("/export-chat")
def export_chat(request: Request, conversation_id: int, format: str = "md"):

    session_id = get_or_create_session_id(request)

    conversation = get_conversation(session_id, conversation_id)

    if not conversation:
        return JSONResponse({"success": False, "error": "المحادثة غير موجودة"}, status_code=404)

    messages = get_chat_history(session_id, conversation_id)

    os.makedirs(EXPORT_FOLDER, exist_ok=True)

    format = format.lower()

    if format == "json":

        filename = _export_filename(conversation, "json")
        path = os.path.join(EXPORT_FOLDER, filename)

        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "title": conversation["title"],
                "files": conversation["files"],
                "messages": messages,
            }, f, ensure_ascii=False, indent=2)

        return FileResponse(path, filename=filename, media_type="application/json")

    if format == "txt":

        filename = _export_filename(conversation, "txt")
        path = os.path.join(EXPORT_FOLDER, filename)

        with open(path, "w", encoding="utf-8") as f:
            f.write(_render_txt_export(conversation, messages))

        return FileResponse(path, filename=filename, media_type="text/plain")

    if format == "pdf":

        filename = _export_filename(conversation, "pdf")
        path = os.path.join(EXPORT_FOLDER, filename)

        try:

            import fitz  # already a project dependency (pdf_reader.py)

            doc = fitz.open()
            page = doc.new_page()

            html_parts = [f"<h2>{conversation['title']}</h2>"]

            for msg in messages:

                speaker = "المستخدم" if msg["role"] == "user" else "Awab AI"

                safe_text = (
                    msg["text"]
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )

                html_parts.append(f"<p><b>{speaker}:</b><br>{safe_text}</p>")

            html = "<div style='direction:rtl;font-family:sans-serif;'>" + "".join(html_parts) + "</div>"

            page.insert_htmlbox(page.rect, html)

            doc.save(path)
            doc.close()

        except Exception as e:

            print("⚠️ PDF export fallback (insert_htmlbox unavailable):", e)

            # best-effort plain-text fallback so export never hard-fails
            import fitz

            doc = fitz.open()
            page = doc.new_page()

            text_content = _render_txt_export(conversation, messages)

            page.insert_textbox(page.rect, text_content, fontsize=10)

            doc.save(path)
            doc.close()

        return FileResponse(path, filename=filename, media_type="application/pdf")

    # default: markdown

    filename = _export_filename(conversation, "md")
    path = os.path.join(EXPORT_FOLDER, filename)

    with open(path, "w", encoding="utf-8") as f:
        f.write(_render_markdown_export(conversation, messages))

    return FileResponse(path, filename=filename, media_type="text/markdown")
