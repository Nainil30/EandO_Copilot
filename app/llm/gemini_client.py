from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class GeminiClient:
    api_key: str

    def generate_text(self, prompt: str, model: str = "gemini-1.5-flash") -> str:
        from google import genai

        client = genai.Client(api_key=self.api_key)
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        return (resp.text or "").strip()


_cached = None


def gemini_client() -> GeminiClient:
    global _cached
    if _cached is not None:
        return _cached

    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is missing. Put it in .env and restart uvicorn.")
    _cached = GeminiClient(api_key=key)
    return _cached


def generate_text(prompt: str, model: str = "gemini-1.5-flash") -> str:
    """
    Convenience wrapper so other modules can do:
      from app.llm.gemini_client import generate_text
    """
    return gemini_client().generate_text(prompt=prompt, model=model)
