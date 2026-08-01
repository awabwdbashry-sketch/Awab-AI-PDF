# app/services/vector_store.py
import os
import re
import json
import pickle
import hashlib
import faiss
import numpy as np

from sentence_transformers import SentenceTransformer


MODEL = None


def get_model():

    global MODEL

    if MODEL is None:

        print("جاري تحميل موديل الذكاء الاصطناعي...")

        MODEL = SentenceTransformer(
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )

        print("تم تحميل الموديل ✅")

    return MODEL


# ---------------------------------------------------------------------------
# DEPLOYMENT FIX: these used to be plain relative strings ("app/uploads/...")
# which only resolve correctly if the process's current working directory
# happens to be the project root. That's true when running locally from the
# project folder, but is NOT guaranteed on every platform/start command, and
# silently breaks (FileNotFoundError / files "disappearing") if the working
# directory is ever different. Building the path from this file's own
# location makes it work no matter where the process is launched from.
# ---------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../app

VECTOR_FOLDER = os.path.join(APP_DIR, "uploads", "vectors")

TEXT_FOLDER = os.path.join(APP_DIR, "uploads", "texts")


os.makedirs(
    VECTOR_FOLDER,
    exist_ok=True
)


def clean_filename(filename):

    filename = os.path.basename(filename).strip()

    filename = " ".join(filename.split())

    while ".." in filename:
        filename = filename.replace("..", ".")

    filename = filename.replace(". .pdf", ".pdf")
    filename = filename.replace(" .pdf", ".pdf")

    return filename


def generate_file_id(filename):

    filename = clean_filename(filename)

    return hashlib.md5(
        filename.encode("utf-8")
    ).hexdigest()


def normalize_arabic_text(text):

    if not text:
        return ""

    text = re.sub(r'[\u064B-\u0652\u0670\u0640]', '', text)
    text = re.sub(r'[إأآا]', 'ا', text)
    text = re.sub(r'ى', 'ي', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def split_into_chunks(text, chunk_size=800, overlap=150):

    pieces = re.split(r'(?<=[\.\!\?\؟\۔])\s+|\n+', text)

    pieces = [p.strip() for p in pieces if p.strip()]

    chunks = []
    current = ""

    for piece in pieces:

        if len(current) + len(piece) + 1 <= chunk_size:

            current = (current + " " + piece).strip() if current else piece

        else:

            if current:
                chunks.append(current.strip())

            if overlap > 0 and chunks:
                tail = chunks[-1][-overlap:]
                current = (tail + " " + piece).strip()
            else:
                current = piece

            while len(current) > chunk_size * 1.5:
                chunks.append(current[:chunk_size].strip())
                current = current[chunk_size - overlap:]

    if current.strip():
        chunks.append(current.strip())

    return chunks


def load_pages(filename):

    text_file = filename.replace(".pdf", ".json")

    text_path = os.path.join(
        TEXT_FOLDER,
        text_file
    )

    if not os.path.exists(text_path):
        return None

    with open(
        text_path,
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def create_vector_store(filename):

    filename = clean_filename(filename)

    print("=" * 50)
    print("إنشاء Vector Store")
    print("الملف:", filename)
    print("=" * 50)

    pages = load_pages(filename)

    if pages is None:

        print("ملف النص غير موجود ❌")

        return False

    if not pages:

        print("الملف فارغ ❌")

        return False

    # chunk PER PAGE and tag every chunk with its page number:
    # {"text": "...", "page": 3}
    chunks = []

    for page_entry in pages:

        page_number = page_entry["page"]
        page_text = page_entry["text"]

        page_chunks = split_into_chunks(page_text)

        for chunk_text in page_chunks:

            chunks.append({
                "text": chunk_text,
                "page": page_number
            })

    print("عدد الأجزاء:", len(chunks))

    if len(chunks) == 0:

        print("لا توجد بيانات ❌")

        return False

    model = get_model()

    print("جاري إنشاء Embeddings...")

    # Embed ONLY chunk["text"] - page metadata is never embedded.
    embedding_inputs = [
        normalize_arabic_text(chunk["text"])
        for chunk in chunks
    ]

    embeddings = model.encode(
        embedding_inputs,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True
    )

    embeddings = np.array(
        embeddings,
        dtype="float32"
    )

    dimension = embeddings.shape[1]

    index = faiss.IndexFlatIP(
        dimension
    )

    index.add(
        embeddings
    )

    file_id = generate_file_id(
        filename
    )

    index_path = os.path.join(
        VECTOR_FOLDER,
        file_id + ".index"
    )

    chunks_path = os.path.join(
        VECTOR_FOLDER,
        file_id + ".pkl"
    )

    print("File ID:", file_id)

    print("Index:", index_path)

    faiss.write_index(
        index,
        index_path
    )

    # pickle stores list of {"text":..., "page":...} dicts
    with open(
        chunks_path,
        "wb"
    ) as f:

        pickle.dump(
            chunks,
            f
        )

    print("تم إنشاء Vector Store ✅")

    print(
        "هل الـ Index موجود؟",
        os.path.exists(index_path)
    )

    print(
        "هل الـ PKL موجود؟",
        os.path.exists(chunks_path)
    )

    return True


# ---------------------------------------------------------------------------
# Purely additive helper (does not touch create_vector_store() above or
# anything about how vectors are built): lets main.py check whether a PDF
# already has an up-to-date vector store on disk before re-embedding it.
# Used for duplicate-upload detection (feature 21 / security requirement)
# so re-uploading the exact same filename doesn't burn time and compute
# re-running SentenceTransformer + FAISS for nothing.
# ---------------------------------------------------------------------------
def vector_store_exists(filename):

    filename = clean_filename(filename)

    file_id = generate_file_id(filename)

    index_path = os.path.join(VECTOR_FOLDER, file_id + ".index")
    chunks_path = os.path.join(VECTOR_FOLDER, file_id + ".pkl")

    return os.path.exists(index_path) and os.path.exists(chunks_path)
