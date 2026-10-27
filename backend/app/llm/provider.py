"""LLM provider abstraction + Google Gemini implementation.

The model only *proposes* plans and SQL as JSON. It never executes SQL, touches files or runs code.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings
from .schemas import to_gemini_schema

log = logging.getLogger("loopsql.llm")
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    retryable: bool = False


class LLMNotConfiguredError(LLMError):
    pass


class LLMRateLimitError(LLMError):
    retryable = True
    retry_after: float | None = None  # seconds suggested by the API (RetryInfo)


class LLMServiceError(LLMError):
    pass


class LLMResponseError(LLMError):
    """The model answered, but not with valid JSON matching the schema."""
    retryable = True


class LLMProvider(Protocol):
    name: str

    def generate_structured(self, *, task: str, system: str, prompt: str, schema: type[T]) -> T: ...


class GeminiProvider:
    def __init__(self, settings: Settings, client=None, sleep=time.sleep):
        self.settings = settings
        self.name = f"gemini:{settings.gemini_model}"
        self._sleep = sleep
        if client is not None:
            self._client = client
        else:
            if not settings.gemini_configured:
                raise LLMNotConfiguredError(
                    "Gemini API key is not configured. Add GEMINI_API_KEY to backend/.env and restart the server.")
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=settings.gemini_api_key.get_secret_value(),  # type: ignore[union-attr]
                http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_seconds * 1000)),
            )

    def generate_structured(self, *, task: str, system: str, prompt: str, schema: type[T]) -> T:
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self.settings.gemini_temperature,
            response_mime_type="application/json",
            response_json_schema=to_gemini_schema(schema),
        )
        attempts = max(1, self.settings.gemini_max_retries + 1)
        last_error: LLMError | None = None
        for attempt in range(attempts):
            try:
                resp = self._client.models.generate_content(
                    model=self.settings.gemini_model, contents=prompt, config=config)
                text = (resp.text or "").strip()
                if not text:
                    raise LLMResponseError("Gemini returned an empty response (possibly blocked by safety filters).")
                return schema.model_validate_json(_strip_fences(text))
            except errors.APIError as e:
                code = getattr(e, "code", None) or 0
                msg = getattr(e, "message", None) or str(e)
                if code == 429:
                    last_error = LLMRateLimitError(f"Gemini rate limit / quota exceeded (429): {msg}")
                    last_error.retry_after = _retry_delay_seconds(getattr(e, "details", None))
                elif code >= 500 or code in (408,):
                    last_error = LLMServiceError(f"Gemini service error ({code}): {msg}")
                    last_error.retryable = True
                elif code in (401, 403):
                    raise LLMServiceError(f"Gemini rejected the API key or permissions ({code}): {msg}") from None
                elif code == 404:
                    raise LLMServiceError(
                        f"Gemini model '{self.settings.gemini_model}' was not found ({code}). "
                        "Set GEMINI_MODEL in backend/.env to a current model.") from None
                else:
                    raise LLMServiceError(f"Gemini request failed ({code}): {msg}") from None
            except (ValidationError, json.JSONDecodeError) as e:
                last_error = LLMResponseError(f"Gemini returned JSON that does not match the {schema.__name__} schema: "
                                              f"{str(e).splitlines()[0]}")
            except LLMResponseError as e:
                last_error = e
            except (TimeoutError, OSError) as e:
                last_error = LLMServiceError(f"Network error talking to Gemini: {type(e).__name__}: {e}")
                last_error.retryable = True
            if attempt < attempts - 1 and last_error.retryable:
                delay = min(20.0, 1.5 * (2 ** attempt))
                server_hint = getattr(last_error, "retry_after", None)
                if server_hint:  # free-tier quotas are per minute; honour Google's RetryInfo hint
                    delay = min(MAX_RETRY_DELAY_SECONDS, max(delay, server_hint))
                log.warning("Gemini %s failed (%s); retrying in %.1fs", task, type(last_error).__name__, delay)
                self._sleep(delay)
                continue
            break
        assert last_error is not None
        raise last_error


MAX_RETRY_DELAY_SECONDS = 60.0


def _retry_delay_seconds(details: Any) -> float | None:
    """Find google.rpc.RetryInfo.retryDelay (e.g. "37s" or "1.5s") anywhere in the error payload."""
    found: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            val = node.get("retryDelay")
            if isinstance(val, str) and val.endswith("s"):
                try:
                    found.append(float(val[:-1]))
                except ValueError:
                    pass
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(details)
    return max(found) if found else None


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return text.strip()


def build_provider(settings: Settings) -> LLMProvider:
    return GeminiProvider(settings)
