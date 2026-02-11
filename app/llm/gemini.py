import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

def get_client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing in .env")
    return genai.Client(api_key=key)

def generate_sql(prompt: str, model: str = "gemini-2.5-flash") -> str:
    client = get_client()
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    # resp.text should be the model output
    return (resp.text or "").strip()
