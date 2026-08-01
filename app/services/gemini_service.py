import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

API_KEY = os.getenv("GOOGLE_API_KEY")

print("===================================")
print("API KEY =", API_KEY)
print("===================================")

genai.configure(api_key=API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")


def ask_gemini(context, question):

    prompt = f"""
السياق:
{context}

السؤال:
{question}
"""

    try:
        response = model.generate_content(prompt)
        return response.text

    except Exception as e:
        return str(e)