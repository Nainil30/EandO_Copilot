from dotenv import load_dotenv
from google import genai
import os

print("1) starting")
load_dotenv()
print("2) env loaded")
print("3) key exists:", bool(os.getenv("GEMINI_API_KEY")))

key = os.getenv("GEMINI_API_KEY")
assert key, "GEMINI_API_KEY missing. Put it in .env at project root"

client = genai.Client()
print("4) client created")

resp = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Reply with exactly: GEMINI_OK"
)

print("5) raw response text:", repr(resp.text))
print(resp.text.strip())

