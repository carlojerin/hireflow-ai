import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing from .env")

client = genai.Client(api_key=API_KEY)


def ask_gemini(prompt: str) -> str:

    chat = client.chats.create(
        model="gemini-3.7-flash"
    )

    response = chat.send_message(
        message=prompt
    )

    if not response.text:
        raise RuntimeError(
            "Gemini returned an empty response."
        )

    return response.text