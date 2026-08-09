"""The LLM boundary.

Everything that talks to a model goes through `LLMClient.complete_json`, which
keeps the surface small enough to mock in tests and to reason about when asking
"can the model introduce text into the resume?". The answer must stay no: the
callers in `jd_parser` and `ranking` validate every response against a schema,
and `ranking` additionally checks that the response only names content that
already existed before the call.
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .errors import LLMError

DEFAULT_MODEL = "gpt-4o-mini"

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LLMClient(Protocol):
    """Anything that can turn a prompt pair into a JSON string."""

    def complete_json(self, system: str, user: str) -> str: ...


class OpenAIClient:
    """OpenAI-compatible chat completions.

    Works against anything that speaks the OpenAI API — OpenAI itself, Ollama,
    OpenRouter, Groq — by pointing `base_url` at it.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        self.model = model or os.environ.get("RESUME_TAILOR_MODEL") or DEFAULT_MODEL
        self.temperature = temperature
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on install state
            raise LLMError(
                "the `openai` package is not installed. Run `uv sync`."
            ) from exc
        if not self._api_key and not self._base_url:
            raise LLMError(
                "no OPENAI_API_KEY set. Export it, or set OPENAI_BASE_URL to a local "
                "OpenAI-compatible server (e.g. http://localhost:11434/v1 for Ollama)."
            )
        self._client = OpenAI(api_key=self._api_key or "unused", base_url=self._base_url)
        return self._client

    def complete_json(self, system: str, user: str) -> str:
        client = self._ensure_client()
        try:
            response = client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # noqa: BLE001 - surface any transport/API failure as one type
            raise LLMError(f"LLM request failed ({self.model}): {exc}") from exc
        content = response.choices[0].message.content
        if not content:
            raise LLMError(f"LLM returned an empty response ({self.model}).")
        return content


def request_validated_json(
    client: LLMClient,
    system: str,
    user: str,
    schema: type[ModelT],
    attempts: int = 2,
    extra_validation=None,
) -> ModelT:
    """Call the model and validate the response, retrying once on failure.

    `extra_validation` runs after schema validation and may raise `ValueError`;
    that error is fed back to the model the same way a schema error is. Callers
    use it for constraints a schema cannot express, such as "every id you
    returned must already exist in the candidate set".
    """

    prompt = user
    last_error = ""
    for attempt in range(attempts):
        raw = client.complete_json(system, prompt)
        try:
            payload = _parse_json(raw)
            result = schema.model_validate(payload)
            if extra_validation is not None:
                extra_validation(result)
            return result
        except (ValueError, ValidationError) as exc:
            last_error = _describe(exc)
            prompt = (
                f"{user}\n\n"
                "Your previous response was rejected. Fix it and return only valid "
                f"JSON matching the schema.\n\nPrevious response:\n{raw}\n\n"
                f"Error:\n{last_error}"
            )
            if attempt == attempts - 1:
                break
    raise LLMError(
        f"the model returned output that failed validation {attempts} times.\n"
        f"Last error:\n{last_error}"
    )


def _parse_json(raw: str) -> object:
    text = raw.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response was not valid JSON: {exc}") from exc


def _describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "\n".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
            for error in exc.errors()
        )
    return str(exc)
