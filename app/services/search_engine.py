import os
import re
import pickle
import hashlib
import faiss
import numpy as np

from sentence_transformers import SentenceTransformer
from app.ai.gemini import ask_gemini


MODEL = None


VECTOR_FOLDER = "app/uploads/vectors"



def get_model():

    global MODEL

    if MODEL is None:

        print("جاري تحميل موديل البحث...")

        MODEL = SentenceTransformer(
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )

        print("تم تحميل الموديل ✅")

    return MODEL





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





MIN_SIMILARITY = 0.35

TOP_K = 5

# When searching across multiple PDFs at once, we still pull TOP_K
# candidates from EACH file (so a file with a great match never gets
# crowded out by a mediocre-but-numerous file), then keep the best
# MULTI_FILE_BEST_N overall for the final context sent to Gemini.
MULTI_FILE_BEST_N = 5




def _load_file_index(filename):
    """
    Loads one PDF's FAISS index + chunk list off disk. Returns
    (index, chunks) or (None, None) if this file has no vector store yet
    (e.g. it's still being processed, or was deleted). Never raises -
    callers just skip a file that isn't ready instead of failing the
    whole multi-file search.
    """

    filename = clean_filename(filename)

    file_id = generate_file_id(filename)

    index_path = os.path.join(VECTOR_FOLDER, file_id + ".index")
    chunks_path = os.path.join(VECTOR_FOLDER, file_id + ".pkl")

    if not os.path.exists(index_path) or not os.path.exists(chunks_path):
        return None, None

    try:

        index = faiss.read_index(index_path)

        with open(chunks_path, "rb") as f:
            chunks = pickle.load(f)

        return index, chunks

    except Exception as e:

        print(f"⚠️ تعذر تحميل بيانات الملف {filename}:", e)

        return None, None



# ---------------------------------------------------------------------------
# Core retrieval step, shared by both the normal (blocking) multi-file
# search and the streaming endpoint. Runs the embedding + FAISS search
# across every filename given, merges the best matches, and returns:
#
#   context  -> plain text block to feed Gemini, e.g.:
#               "[Book.pdf - Page 10]\n...\n\n[Lecture.pdf - Page 4]\n..."
#   sources  -> [{"filename": "Book.pdf", "page": 10}, ...]  (deduped,
#               ordered by relevance, capped at 3 per the previous
#               single-file behavior)
#
# Returns (None, []) if NONE of the files have usable indexes/chunks, and
# ("", []) if indexes exist but nothing scored above the relevance floor
# for any of them - callers use this to produce the right status message.
# ---------------------------------------------------------------------------
def gather_context(filenames, question):

    filenames = [clean_filename(f) for f in filenames if f]

    if not filenames:
        return None, []

    model = get_model()

    normalized_question = normalize_arabic_text(question)

    question_vector = model.encode(
        [normalized_question],
        convert_to_numpy=True,
        normalize_embeddings=True
    ).astype("float32")

    all_candidates = []  # list of (score, filename, chunk_text, page)
    any_file_loaded = False

    for filename in filenames:

        index, chunks = _load_file_index(filename)

        if index is None or not chunks:
            continue

        any_file_loaded = True

        k = min(TOP_K, len(chunks))

        scores, indices = index.search(question_vector, k)

        for idx, score in zip(indices[0], scores[0]):

            if idx != -1 and 0 <= idx < len(chunks):

                chunk = chunks[idx]

                all_candidates.append((
                    float(score),
                    filename,
                    chunk["text"],
                    chunk["page"]
                ))

    if not any_file_loaded:
        return None, []

    if not all_candidates:
        return "", []

    relevant = [c for c in all_candidates if c[0] >= MIN_SIMILARITY]

    if not relevant:
        # same "no strong match, but at least give the closest one"
        # fallback the original single-file logic used
        relevant = [max(all_candidates, key=lambda c: c[0])]

    relevant.sort(key=lambda c: c[0], reverse=True)

    best_results = relevant[:MULTI_FILE_BEST_N]

    context = ""
    sources = []

    for score, filename, text, page in best_results:

        context += f"\n\n[{filename} - Page {page}]\n\n{text}\n"

        source = {"filename": filename, "page": page}

        if source not in sources:
            sources.append(source)

    return context, sources[:6]



# ---------------------------------------------------------------------------
# search_in_files() ALWAYS returns exactly this shape:
#
#   {
#       "answer": "plain text AI answer - never HTML",
#       "sources": [{"filename": "Book.pdf", "page": 10}, ...]
#   }
#
# This is the multi-PDF-aware counterpart of search_in_text() below.
# main.py uses this one for the normal (non-streaming) /chat fallback;
# the /chat-stream endpoint calls gather_context() + ask_gemini_stream()
# directly instead, so it can stream tokens as they arrive.
# ---------------------------------------------------------------------------
def search_in_files(filenames, question):

    print("=" * 60)
    print("🚀 دخلنا search_in_files")
    print("الملفات:", filenames)
    print("السؤال:", question)
    print("=" * 60)

    context, sources = gather_context(filenames, question)

    if context is None:

        return {
            "answer": "لم يتم العثور على قاعدة البيانات الخاصة بالملفات.",
            "sources": []
        }

    if not context.strip():

        return {
            "answer": "لم يتم العثور على نص مناسب داخل الملفات.",
            "sources": []
        }

    print(context[:1000])

    try:

        answer = ask_gemini(context, question)

        answer = str(answer) if answer is not None else ""

        return {
            "answer": answer,
            "sources": sources
        }

    except Exception as e:

        print("خطأ Gemini:", e)

        return {
            "answer": f"حدث خطأ: {e}",
            "sources": []
        }



# ---------------------------------------------------------------------------
# search_in_text() ALWAYS returns exactly this shape, so main.py can rely
# on it without extra parsing:
#
#   {
#       "filename": "file.pdf",
#       "answer": "plain text AI answer - never HTML",
#       "pages": [1, 2, 3]
#   }
#
# UNCHANGED from before - kept for backward compatibility with anything
# still calling the single-file API. New code should prefer
# search_in_files() above, which also works fine with a single filename.
# ---------------------------------------------------------------------------
def search_in_text(filename, question):


    filename = clean_filename(filename)


    print("=" * 60)
    print("🚀 دخلنا search_in_text")
    print("الملف:", filename)
    print("السؤال:", question)
    print("=" * 60)




    file_id = generate_file_id(filename)



    index_path = os.path.join(
        VECTOR_FOLDER,
        file_id + ".index"
    )


    chunks_path = os.path.join(
        VECTOR_FOLDER,
        file_id + ".pkl"
    )





    if not os.path.exists(index_path):

        return {
            "filename": filename,
            "answer": "لم يتم العثور على قاعدة البيانات الخاصة بالملف.",
            "pages": []
        }



    if not os.path.exists(chunks_path):

        return {
            "filename": filename,
            "answer": "لم يتم العثور على بيانات الملف.",
            "pages": []
        }





    index = faiss.read_index(
        index_path
    )





    with open(
        chunks_path,
        "rb"
    ) as f:

        chunks = pickle.load(f)





    print(
        "عدد الأجزاء:",
        len(chunks)
    )





    model = get_model()





    normalized_question = normalize_arabic_text(question)





    question_vector = model.encode(
        [normalized_question],
        convert_to_numpy=True,
        normalize_embeddings=True
    ).astype("float32")





    k = min(
        TOP_K,
        len(chunks)
    )





    scores, indices = index.search(
        question_vector,
        k
    )





    print(
        "Scores:",
        scores
    )





    candidates = []



    for idx, score in zip(indices[0], scores[0]):

        if idx != -1 and 0 <= idx < len(chunks):

            candidates.append(
                (idx, score)
            )





    relevant = []



    for idx, score in candidates:

        if score >= MIN_SIMILARITY:

            relevant.append(
                (idx, score)
            )





    if not relevant and candidates:

        relevant.append(
            max(
                candidates,
                key=lambda x:x[1]
            )
        )





    relevant = sorted(
        relevant,
        key=lambda x:x[1],
        reverse=True
    )





    best_results = relevant[:3]





    context = ""

    used_pages = []





    for idx, score in best_results:


        chunk = chunks[idx]


        page = chunk["page"]



        context += f"""

[Page {page}]

{chunk["text"]}

"""



        used_pages.append(page)





    print("=" * 60)
    print(context[:1000])
    print("=" * 60)





    if not context.strip():

        return {
            "filename": filename,
            "answer": "لم يتم العثور على نص مناسب داخل الملف.",
            "pages": []
        }







    try:


        answer = ask_gemini(
            context,
            question
        )


        # Defensive: guarantee a plain string even if ask_gemini ever
        # returns something else (e.g. None), so main.py / chat.html
        # never has to deal with a non-string "answer".
        answer = str(answer) if answer is not None else ""


        return {

            "filename": filename,

            "answer": answer,

            "pages": sorted(
                set(used_pages[:3])
            )

        }






    except Exception as e:


        print(
            "خطأ Gemini:",
            e
        )


        return {

            "filename": filename,

            "answer": f"حدث خطأ: {e}",

            "pages": []

        }
