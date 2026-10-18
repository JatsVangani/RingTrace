"""One chat call that returns JSON. Local llama.cpp by default; any OpenAI-compatible host
(Groq, Gemini, OpenRouter, ...) by setting LLM_BASE_URL, LLM_MODEL and LLM_API_KEY."""

import json
import re
from functools import lru_cache

from openai import BadRequestError, OpenAI

from ringtrace_core.config import get_settings


@lru_cache(maxsize=1)
def get_llm() -> OpenAI:
    s = get_settings()
    # llama.cpp ignores the key, but the client refuses to start without one.
    return OpenAI(
        base_url=s.llm_base_url, api_key=s.llm_api_key or "local", timeout=s.llm_timeout_s
    )


def is_local() -> bool:
    return get_settings().llm_api_key is None


def complete_json(system: str, user: str, schema: dict, max_tokens: int = 400) -> dict:
    """Ask for an answer that matches `schema`; returns it parsed."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    params = {"model": get_settings().llm_model, "temperature": 0, "max_tokens": max_tokens}
    # llama.cpp: no hidden "thinking" pass, so the schema applies to the whole reply.
    extra = {"chat_template_kwargs": {"enable_thinking": False}} if is_local() else None
    try:
        response = get_llm().chat.completions.create(
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema},
            },
            extra_body=extra,
            **params,
        )
    except BadRequestError:
        # Some hosts and model files refuse a schema; ask for plain JSON and spell it out.
        messages[0]["content"] += (
            f"\n\nReply with only a JSON object matching this schema:\n{json.dumps(schema)}"
        )
        response = get_llm().chat.completions.create(messages=messages, extra_body=extra, **params)
    text = response.choices[0].message.content
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # empty think block, if any
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)
