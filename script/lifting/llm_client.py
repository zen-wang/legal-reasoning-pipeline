"""
HTTP client for vLLM OpenAI-compatible endpoint.

Sends chat completion requests and extracts JSON from responses.
Uses `requests` (already installed) — no `openai` package needed.
"""

from __future__ import annotations

import json
import logging
import re

import requests

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8000"
DEFAULT_MODEL = "/data/datasets/community/huggingface/models--meta-llama--Llama-3.3-70B-Instruct/snapshots/6f6073b423013f6a7d4d9f39144961bfbfbc386b"
DEFAULT_TIMEOUT = 300
DEFAULT_MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# JSON extraction with fallbacks
# ---------------------------------------------------------------------------

def extract_json(raw: str) -> dict | None:
    """
    Extract JSON from LLM response with fallbacks.

    1. Strip markdown fences if present
    2. Try json.loads() on cleaned text
    3. On failure, regex extract first {...} block
    """
    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines).strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: extract first {...} block (greedy, handles nested braces)
    match = re.search(r"\{", text)
    if match:
        start = match.start()
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    logger.warning("Failed to extract JSON from LLM response")
    return None


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

class LLMClient:
    """HTTP client for vLLM OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
    ) -> tuple[str, dict | None]:
        """
        Send a chat completion request to the vLLM server.

        Returns (raw_response_text, parsed_json_or_none).
        Raises ConnectionError on network failure.
        """
        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": max_tokens or self.max_tokens,
        }

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            raise ConnectionError(f"Cannot connect to LLM server at {self.base_url}")
        except requests.exceptions.Timeout:
            raise TimeoutError(f"LLM request timed out after {self.timeout}s")
        except requests.exceptions.HTTPError as e:
            body = resp.text[:500] if resp is not None else ""
            raise RuntimeError(f"LLM server error: {e} | {body}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return ("", None)

        raw_text = choices[0].get("message", {}).get("content", "")
        parsed = extract_json(raw_text)

        return (raw_text, parsed)
