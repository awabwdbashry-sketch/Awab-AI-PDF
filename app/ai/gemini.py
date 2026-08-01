import os

from dotenv import load_dotenv
from google import genai


load_dotenv()


API_KEY = os.getenv("GOOGLE_API_KEY")


print("=" * 50)
print("API KEY موجود؟", API_KEY is not None)


if API_KEY:
    print("بداية المفتاح:", API_KEY[:10] + "...")
else:
    print("لم يتم العثور على GOOGLE_API_KEY")


print("=" * 50)



client = genai.Client(
    api_key=API_KEY
)


MODEL_NAME = "gemini-flash-lite-latest"



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
