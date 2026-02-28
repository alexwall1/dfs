import json
import logging
import os
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

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


def _strip_code_fences(text: str) -> str:
    """Ta bort markdown code fences om modellen formaterat svaret felaktigt."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_tool_arguments(args: Any) -> dict:
    """Ollama kan returnera tool-arguments som sträng eller som objekt."""
    if isinstance(args, str):
        return json.loads(args)
    return args if args else {}


def _run_tool(tool_name: str, tool_args: dict, dfs2_get_arende_func) -> str:
    """Kör ett tool-anrop och returnera resultatet som JSON-sträng."""
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


def _call_ollama(messages: list, tools: list | None = None, format_json: bool = False) -> dict:
    """Gör ett anrop mot Ollama /api/chat och returnera svaret."""
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

    logger.debug(
        f"Anropar Ollama: model={OLLAMA_MODEL}, messages={len(messages)}, "
        f"tools={bool(tools)}, format_json={format_json}"
    )
    t0 = time.monotonic()
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
    elapsed = time.monotonic() - t0
    msg = data.get("message", {})
    logger.debug(
        f"Ollama svarade på {elapsed:.1f}s: "
        f"stop_reason={data.get('done_reason')}, "
        f"has_tool_calls={bool(msg.get('tool_calls'))}, "
        f"content_len={len(msg.get('content') or '')}"
    )
    return data


def _tool_calling_loop(messages: list, dfs2_get_arende_func) -> dict:
    """
    Kör Ollama tool-calling loop tills modellen returnerar ett svar utan tool_calls.
    Returnerar det parsade JSON-svaret.
    Modifierar messages in-place med tool-anropshistorik.
    """
    for iteration in range(5):
        logger.debug(f"Tool-calling loop iteration {iteration}, messages={len(messages)}")
        response = _call_ollama(messages, tools=TOOLS)
        message = response.get("message", {})

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            content = message.get("content", "")
            logger.debug(f"Ollama final content (raw, first 500 chars): {content[:500]}")
            content = _strip_code_fences(content)
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON from Ollama (iteration {iteration}): {e}")
                logger.warning(f"Raw content was: {content[:1000]}")
                raise ValueError(f"Ollama returnerade ogiltigt JSON: {content[:200]}")

        # Lägg till assistent-meddelandet med tool_calls i historiken
        messages.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": tool_calls,
        })

        # Exekvera tool-anropen och lägg till resultaten
        for tool_call in tool_calls:
            func = tool_call.get("function", {})
            tool_name = func.get("name", "")
            try:
                tool_args = _parse_tool_arguments(func.get("arguments", {}))
            except (json.JSONDecodeError, Exception):
                tool_args = {}

            result = _run_tool(tool_name, tool_args, dfs2_get_arende_func)
            logger.info(f"Tool: {tool_name}({tool_args}) → {result[:200]}")
            messages.append({"role": "tool", "content": result})

    raise ValueError("Ollama tool-calling loop avslutades utan svar efter 5 iterationer")


def extrahera_handling(
    email_text: str,
    from_email: str,
    subject: str,
    attachments: list,
    dfs2_get_arende_func,
) -> dict:
    """
    Kör Ollama tool-calling loop för att extrahera handlingsinformation från ett mejl.
    dfs2_get_arende_func: callable(diarienummer: str) -> dict | None
    Returnerar dict med proposed_handling.
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
    Kör om extraktion med uppdaterade instruktioner baserat på befintlig konversationshistorik.
    conversation_history ska INTE redan innehålla new_instructions som sista meddelande.
    """
    messages = list(conversation_history)
    messages.append({"role": "user", "content": new_instructions})
    return _tool_calling_loop(messages, dfs2_get_arende_func)


def klassificera_svar(text: str) -> str:
    """
    Klassificerar ett svar från handläggaren.
    Returnerar "confirm", "cancel" eller "update".
    Provar keyword-matchning först, faller tillbaka på Ollama vid osäkerhet.
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

    # Ollama för mer komplexa fall
    try:
        messages = [
            {"role": "system", "content": KLASSIFICERING_PROMPT},
            {"role": "user", "content": f"Svar att klassificera: {text}"},
        ]
        response = _call_ollama(messages, format_json=True)
        content = response.get("message", {}).get("content", "")
        content = _strip_code_fences(content)
        result = json.loads(content)
        action = result.get("action", "update")
        if action in ("confirm", "cancel", "update"):
            return action
    except Exception as e:
        logger.warning(f"Ollama classification failed: {e}, defaulting to 'update'")

    return "update"
