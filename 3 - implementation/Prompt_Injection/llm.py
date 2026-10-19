"""One function to call any OpenAI-compatible chat endpoint.

Works with Ollama (http://localhost:11434/v1), vLLM, LM Studio, OpenAI, etc.
Set via environment variables:
    LLM_BASE_URL   e.g. http://localhost:11434/v1
    LLM_API_KEY    e.g. ollama   (any non-empty string for local servers)
    LLM_MODEL      e.g. llama3.1:8b
"""
import os
import time

from openai import OpenAI

_client = OpenAI(base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
                 api_key=os.getenv("LLM_API_KEY", "ollama"))
DEFAULT_MODEL = os.getenv("LLM_MODEL", "llama3.1:8b")


def chat(system, user, model=None, temperature=0.0, max_tokens=512, retries=3):
    for attempt in range(retries):
        try:
            r = _client.chat.completions.create(
                model=model or DEFAULT_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return r.choices[0].message.content or ""
        except Exception as e:  # network hiccups, rate limits
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
