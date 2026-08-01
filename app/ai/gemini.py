import os

# python-dotenv is a LOCAL-ONLY convenience: it loads variables from a
# ".env" file into the process environment when one exists (developer's
# machine). On Railway there is no ".env" file in the container - Railway
# injects environment variables (GOOGLE_API_KEY, PORT, etc.) directly into
# the process environment - so load_dotenv() simply finds nothing to load
# and is a harmless no-op there. It also never overrides variables that
# are already set, so it can never clobber Railway's env vars even if a
# stray .env file were present. Import is wrapped defensively so a missing
# package/.env file can never crash startup either.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as e:
    print("⚠️ dotenv not loaded (fine on Railway / production):", e)

from google import genai


MODEL_NAME = "gemini-flash-lite-latest"


# ---------------------------------------------------------------------------
# IMPORTANT (deployment fix): the Gemini client is NEVER created at import
# time anymore. Creating it at import time meant that simply importing this
# module (which main.py does indirectly through search_engine.py) would try
# to build an API client before FastAPI had even started - and before we
# could be sure GOOGLE_API_KEY was actually available in the environment.
# On some platforms/import orders that turned a missing/late env var into a
# hard crash at process startup instead of a normal, recoverable runtime
# error.
#
# Now the client is created lazily, on first actual use, via get_client().
# The API key is re-read from the environment every time get_client() is
# called the first time, so it always sees whatever Railway injected.
# ---------------------------------------------------------------------------
_client = None
_client_error_logged = False


def get_api_key():
    return os.getenv("GOOGLE_API_KEY")


def get_client():

    global _client, _client_error_logged

    if _client is not None:
        return _client

    api_key = get_api_key()

    if not api_key and not _client_error_logged:
        print("=" * 50)
        print("⚠️ لم يتم العثور على GOOGLE_API_KEY في متغيرات البيئة")
        print("=" * 50)
        _client_error_logged = True

    # google-genai will happily raise a clear error the moment it's asked
    # to actually call the API with no/invalid key - which is what we want:
    # a normal per-request error, not a process-level crash at import time.
    _client = genai.Client(api_key=api_key)

    return _client


def build_prompt(context, question):

    return f"""

أنت مساعد ذكي لتحليل ملفات PDF.

لديك نص مستخرج من ملف (أو عدة ملفات).

قواعد الإجابة:

1- إذا كانت الإجابة موجودة داخل النص:
- أجب مباشرة.
- اعتمد على معلومات الملف فقط.
- لا تضف معلومات غير موجودة.

2- إذا لم تجد الإجابة داخل النص:
- قل أولاً:
"لم أجد هذه المعلومة داخل الملف."

- ثم قدم إجابة عامة مفيدة على السؤال إذا كان لديك معرفة بها.
- لا تقل أن الإجابة من الملف.

3- إذا كان السؤال غير واضح اطلب توضيح.


=========================
نص الملف:

{context}

=========================

السؤال:

{question}

"""


def ask_gemini(context, question):
    """
    UNCHANGED non-streaming call - kept exactly as before so nothing that
    already relies on it (e.g. any non-streaming fallback) breaks.
    """

    prompt = build_prompt(context, question)

    try:

        print("📤 جاري إرسال الطلب إلى Gemini...")

        client = get_client()

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt
        )

        print("✅ تم استلام الرد")

        return response.text

    except Exception as e:

        print("❌ Gemini Error:")
        print(e)

        return f"حدث خطأ: {e}"


def ask_gemini_stream(context, question):
    """
    Generator version of ask_gemini(): yields the answer text incrementally
    as Gemini produces it, instead of waiting for the full response. This
    is what powers real token-by-token streaming in the UI.

    Uses the same prompt/model/rules as ask_gemini() - the only difference
    is generate_content_stream(...) instead of generate_content(...).

    On error, yields a single Arabic error string (matching the shape
    ask_gemini() already returns on failure) so the caller can just stream
    whatever this yields straight to the client either way.
    """

    prompt = build_prompt(context, question)

    try:

        print("📤 جاري إرسال طلب Streaming إلى Gemini...")

        client = get_client()

        stream = client.models.generate_content_stream(
            model=MODEL_NAME,
            contents=prompt
        )

        for chunk in stream:

            piece = getattr(chunk, "text", None)

            if piece:
                yield piece

        print("✅ اكتمل الـ Streaming")

    except Exception as e:

        print("❌ Gemini Streaming Error:")
        print(e)

        yield f"حدث خطأ: {e}"


def generate_conversation_title(question, answer=""):
    """
    Feature 2 (Automatic Conversation Title): asks Gemini for a short
    (3-6 word) title summarizing what this conversation is about, based
    on the first user question (and, if available, the first answer).

    Always returns a plain string. Falls back to a trimmed version of the
    question itself if the API call fails, so a Gemini hiccup never
    breaks conversation creation - it just means a slightly less polished
    fallback title instead of a generated one.
    """

    fallback = (question or "محادثة جديدة").strip()

    if len(fallback) > 40:
        fallback = fallback[:40].strip() + "…"

    title_prompt = f"""
اقترح عنوانًا قصيرًا جدًا (من 2 إلى 5 كلمات فقط) يلخص موضوع هذه المحادثة،
بدون علامات تنصيص وبدون نقطة في النهاية. أعد العنوان فقط بدون أي شرح.

سؤال المستخدم:
{question}

{"إجابة مختصرة: " + answer[:300] if answer else ""}
"""

    try:

        client = get_client()

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=title_prompt
        )

        title = (response.text or "").strip().strip('"').strip("'").strip()

        # keep it short even if the model ignores the instruction
        if len(title) > 60:
            title = title[:60].strip() + "…"

        return title or fallback

    except Exception as e:

        print("❌ Gemini Title Error:", e)

        return fallback
