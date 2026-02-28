import json
import logging
import os
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

AI_BACKEND = os.environ.get("AI_BACKEND", "ollama").lower()  # "ollama" | "openrouter"

# Ollama
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")

# OpenRouter
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.2-3b-instruct")


def _get_openrouter_api_key() -> str:
    try:
        with open("/run/secrets/openrouter_api_key") as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.environ.get("OPENROUTER_API_KEY", "")


logger.info(f"AI backend: {AI_BACKEND!r}, model: {OPENROUTER_MODEL if AI_BACKEND == 'openrouter' else OLLAMA_MODEL!r}")

# ── Tools & prompts ────────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "hamta_arende",
            "description": (
                "Hämtar information om ett ärende från DFS2 via diarienummer. "
                "Anropa denna funktion för att verifiera att diarienumret finns "
                "och hämta ärendets detaljer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "diarienummer": {
                        "type": "string",
                        "description": "Ärendets diarienummer, t.ex. DNR-2026-0001",
                    }
                },
                "required": ["diarienummer"],
            },
        },
    }
]

from prompts import KLASSIFICERING_PROMPT, SYSTEM_PROMPT

# ── Utilities ──────────────────────────────────────────────────────────────────


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if the model wrapped its response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_tool_arguments(args: Any) -> dict:
    """Ollama may return tool arguments as a string or as an object; normalise to dict."""
    if isinstance(args, str):
        return json.loads(args)
    return args if args else {}


def _run_tool(tool_name: str, tool_args: dict, dfs2_get_arende_func) -> str:
    """Execute a tool call and return the result as a JSON string."""
    if tool_name == "hamta_arende":
        diarienummer = tool_args.get("diarienummer", "")
        try:
            result = dfs2_get_arende_func(diarienummer)
            if result:
                return json.dumps(result, ensure_ascii=False)
            return json.dumps({"fel": f"Ärende med diarienummer '{diarienummer}' hittades inte."})
        except Exception as e:
            logger.warning(f"Tool call hamta_arende failed: {e}")
            return json.dumps({"fel": str(e)})
    return json.dumps({"fel": f"Okänt tool: {tool_name}"})


# ── Ollama backend ─────────────────────────────────────────────────────────────


def _call_ollama(messages: list, tools: list | None = None, format_json: bool = False) -> dict:
    """
    POST to Ollama /api/chat.

    Returns a normalised dict:
        {
            "content":    str | None,
            "tool_calls": [{"id": str, "name": str, "arguments": dict}] | None,
            "_raw_msg":   dict,   # original message object, for preserving history format
        }
    """
    payload: dict = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    if tools:
        payload["tools"] = tools
    if format_json:
        payload["format"] = "json"

    t0 = time.monotonic()
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    elapsed = time.monotonic() - t0

    msg = data.get("message", {})
    raw_tool_calls = msg.get("tool_calls")

    normalized_tool_calls = None
    if raw_tool_calls:
        normalized_tool_calls = [
            {
                "id": "",  # Ollama does not assign tool call IDs
                "name": tc.get("function", {}).get("name", ""),
                "arguments": _parse_tool_arguments(tc.get("function", {}).get("arguments", {})),
            }
            for tc in raw_tool_calls
        ]

    logger.debug(
        f"Ollama {elapsed:.1f}s: done_reason={data.get('done_reason')}, "
        f"tool_calls={bool(normalized_tool_calls)}, content_len={len(msg.get('content') or '')}"
    )
    return {
        "content": msg.get("content") or None,
        "tool_calls": normalized_tool_calls,
        "_raw_msg": msg,
    }


# ── OpenRouter backend ─────────────────────────────────────────────────────────


def _call_openrouter(messages: list, tools: list | None = None, format_json: bool = False) -> dict:
    """
    POST to OpenRouter /chat/completions (OpenAI-compatible).

    Returns the same normalised dict as _call_ollama.
    """
    api_key = _get_openrouter_api_key()
    if not api_key:
        raise RuntimeError(
            "OpenRouter API-nyckel saknas. Sätt OPENROUTER_API_KEY eller skapa "
            "secrets/openrouter_api_key.txt."
        )

    payload: dict = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }
    if tools:
        payload["tools"] = tools
    if format_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    t0 = time.monotonic()
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(f"{_OPENROUTER_BASE_URL}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    elapsed = time.monotonic() - t0

    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    raw_tool_calls = msg.get("tool_calls")

    normalized_tool_calls = None
    if raw_tool_calls:
        normalized_tool_calls = [
            {
                "id": tc.get("id", ""),
                "name": tc.get("function", {}).get("name", ""),
                "arguments": _parse_tool_arguments(tc.get("function", {}).get("arguments", {})),
            }
            for tc in raw_tool_calls
        ]

    logger.debug(
        f"OpenRouter {elapsed:.1f}s: finish_reason={choice.get('finish_reason')}, "
        f"tool_calls={bool(normalized_tool_calls)}, content_len={len(msg.get('content') or '')}"
    )
    return {
        "content": msg.get("content") or None,
        "tool_calls": normalized_tool_calls,
        "_raw_msg": msg,
    }


# ── Dispatcher ─────────────────────────────────────────────────────────────────


def _call_llm(messages: list, tools: list | None = None, format_json: bool = False) -> dict:
    if AI_BACKEND == "openrouter":
        return _call_openrouter(messages, tools, format_json)
    return _call_ollama(messages, tools, format_json)


# ── Tool-calling loop ──────────────────────────────────────────────────────────


def _tool_calling_loop(messages: list, dfs2_get_arende_func) -> dict:
    """
    Run the LLM in a loop until it responds without tool_calls, then parse and return the JSON.
    Modifies messages in-place so callers can persist the full conversation history.
    """
    for iteration in range(5):
        logger.debug(f"Tool-calling loop iteration {iteration}, messages={len(messages)}")
        response = _call_llm(messages, tools=TOOLS)

        tool_calls = response.get("tool_calls")
        if not tool_calls:
            content = response.get("content") or ""
            logger.debug(f"LLM final content (raw, first 500 chars): {content[:500]}")
            content = _strip_code_fences(content)
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON from LLM (iteration {iteration}): {e}")
                logger.warning(f"Raw content was: {content[:1000]}")
                raise ValueError(f"LLM returnerade ogiltigt JSON: {content[:200]}")

        # Append the assistant turn to history in the backend's native format.
        # _raw_msg is the original message object from the response, so tool_calls
        # already carry the right structure (including IDs for OpenRouter).
        raw_msg = response["_raw_msg"]
        messages.append({
            "role": "assistant",
            "content": raw_msg.get("content") or "",
            "tool_calls": raw_msg.get("tool_calls"),
        })

        # Execute each tool call and append the result.
        for tc in tool_calls:
            result = _run_tool(tc["name"], tc["arguments"], dfs2_get_arende_func)
            logger.info(f"Tool: {tc['name']}({tc['arguments']}) → {result[:200]}")

            tool_msg: dict = {"role": "tool", "content": result}
            if tc.get("id"):
                # OpenRouter (OpenAI-compatible) requires tool_call_id to link
                # the result back to the specific tool call.
                tool_msg["tool_call_id"] = tc["id"]
            messages.append(tool_msg)

    raise ValueError("LLM tool-calling loop avslutades utan svar efter 5 iterationer")


# ── Public API ─────────────────────────────────────────────────────────────────


def extrahera_handling(
    email_text: str,
    from_email: str,
    subject: str,
    attachments: list,
    dfs2_get_arende_func,
) -> dict:
    """
    Extract handling information from an email using the configured LLM backend.
    Returns a dict with the proposed handling fields.
    """
    user_content = f"Från: {from_email}\nÄmne: {subject}\n\nMeddelandetext:\n{email_text}"
    if attachments:
        filnamn = [a.get("filename", "okänd") for a in attachments]
        user_content += f"\nBilagor: {', '.join(filnamn)}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return _tool_calling_loop(messages, dfs2_get_arende_func)


def re_extrahera_handling(
    conversation_history: list,
    new_instructions: str,
    dfs2_get_arende_func,
) -> dict:
    """
    Re-run extraction with updated instructions appended to the existing history.
    conversation_history must NOT already contain new_instructions as the last message.
    """
    messages = list(conversation_history)
    messages.append({"role": "user", "content": new_instructions})
    return _tool_calling_loop(messages, dfs2_get_arende_func)


def klassificera_svar(text: str) -> str:
    """
    Classify a reply as "confirm", "cancel", or "update".
    Uses keyword matching first, falls back to the LLM for ambiguous cases.
    """
    text_lower = text.lower().strip()

    confirm_keywords = ["ja", "ok", "bra", "bekräfta", "skapa", "registrera", "yes", "proceed", "fortsätt", "ja tack"]
    cancel_keywords = ["nej", "avbryt", "stoppa", "avbryta", "cancel", "nej tack", "avbruten", "no"]

    for kw in cancel_keywords:
        if text_lower == kw or text_lower.startswith(kw + " ") or text_lower.startswith(kw + ","):
            return "cancel"
    for kw in confirm_keywords:
        if text_lower == kw or text_lower.startswith(kw + " ") or text_lower.startswith(kw + ","):
            return "confirm"

    try:
        messages = [
            {"role": "system", "content": KLASSIFICERING_PROMPT},
            {"role": "user", "content": f"Svar att klassificera: {text}"},
        ]
        response = _call_llm(messages, format_json=True)
        content = response.get("content") or ""
        content = _strip_code_fences(content)
        result = json.loads(content)
        action = result.get("action", "update")
        if action in ("confirm", "cancel", "update", "unclear"):
            return action
    except Exception as e:
        logger.warning(f"LLM classification failed: {e}, defaulting to 'update'")

    return "update"
