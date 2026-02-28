"""
mail-worker: IMAP poll-loop + SMTP sender + HTTP client mot ai-registrator.

Flöde:
  - Nytt mejl (inget [REF:] i subject) → POST /process → skicka bekräftelsemejl
  - Svar på förslag ([REF:{uuid}] i subject eller body) → POST /sessions/{id}/reply → skicka svar
"""

import base64
import email
import email.header
import email.utils
import json
import logging
import os
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
import imapclient

_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=_log_level, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Konfiguration ─────────────────────────────────────────────────────────────

IMAP_HOST = os.environ.get("IMAP_HOST", "")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER = os.environ.get("IMAP_USER", "")
IMAP_FOLDER = os.environ.get("IMAP_FOLDER", "INBOX")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER
AI_REGISTRATOR_URL = os.environ.get("AI_REGISTRATOR_URL", "http://ai-registrator:8000")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15 MB totalt per mejl
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg"}

REF_PATTERN = re.compile(r"\[REF:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]", re.IGNORECASE)


# ── Sekretläsning ─────────────────────────────────────────────────────────────

def _read_secret(secret_name: str, env_fallback: str = "") -> str:
    try:
        with open(f"/run/secrets/{secret_name}") as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.environ.get(env_fallback, "")


def _imap_password() -> str:
    return _read_secret("imap_password", "IMAP_PASSWORD")


def _smtp_password() -> str:
    return _read_secret("smtp_password", "SMTP_PASSWORD")


def _ai_api_key() -> str:
    return _read_secret("ai_api_key", "AI_API_KEY")


# ── E-postparsning ────────────────────────────────────────────────────────────

def _extract_text(msg: email.message.Message) -> str:
    """Extraherar ren text ur ett e-postmeddelande."""
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if (
                part.get_content_type() == "text/plain"
                and part.get_content_disposition() != "attachment"
            ):
                charset = part.get_content_charset() or "utf-8"
                parts.append(part.get_payload(decode=True).decode(charset, errors="replace"))
    else:
        charset = msg.get_content_charset() or "utf-8"
        parts.append(msg.get_payload(decode=True).decode(charset, errors="replace"))
    return "\n".join(parts)


def _extract_attachments(msg: email.message.Message) -> list[dict]:
    """Extraherar bilagor av tillåten typ och inom storleksgränsen."""
    attachments = []
    total_size = 0

    if not msg.is_multipart():
        return []

    for part in msg.walk():
        disposition = part.get_content_disposition()
        if disposition not in ("attachment", "inline"):
            continue
        filename = part.get_filename()
        if not filename:
            continue
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            logger.info(f"Hoppar över bilaga '{filename}': tillägget är inte tillåtet")
            continue
        data = part.get_payload(decode=True)
        if not data:
            continue
        if total_size + len(data) > MAX_ATTACHMENT_BYTES:
            logger.warning(f"Hoppar över bilaga '{filename}': totalstorleken överskrider 15 MB")
            continue
        total_size += len(data)
        attachments.append({
            "filename": filename,
            "mime_type": part.get_content_type() or "application/octet-stream",
            "data_b64": base64.b64encode(data).decode(),
        })
    return attachments


def _decode_header(raw: str) -> str:
    """Dekoderar MIME-kodade e-posthuvuden (RFC 2047)."""
    parts = email.header.decode_header(raw or "")
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _find_ref(text: str) -> str | None:
    """Hittar [REF:{uuid}] i en sträng och returnerar session-id, annars None."""
    m = REF_PATTERN.search(text or "")
    return m.group(1) if m else None


# ── E-postsändning ────────────────────────────────────────────────────────────

def _send_email(to: str, subject: str, body: str) -> None:
    """Skickar ett e-postmeddelande via SMTP med STARTTLS."""
    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = email.utils.make_msgid()
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, _smtp_password())
            server.send_message(msg)
        logger.info(f"Skickade mejl till {to}: {subject!r}")
    except Exception as e:
        logger.error(f"Misslyckades att skicka mejl till {to}: {e}")


# ── AI-registrator-anrop ──────────────────────────────────────────────────────

def _ai_headers() -> dict:
    return {"Authorization": f"Bearer {_ai_api_key()}"}


def _process_new_email(
    client: httpx.Client,
    from_email: str,
    subject: str,
    body: str,
    attachments: list[dict],
    raw_eml: bytes,
) -> None:
    """Skickar ett nytt mejl till ai-registrator och vidarebefordrar bekräftelsen."""
    payload = {
        "from_email": from_email,
        "subject": subject,
        "body_text": body,
        "attachments": attachments,
        "eml_b64": base64.b64encode(raw_eml).decode(),
    }
    resp = client.post(
        f"{AI_REGISTRATOR_URL}/process",
        json=payload,
        headers=_ai_headers(),
        timeout=400.0,
    )
    if resp.status_code == 403:
        detail = resp.json().get("detail", "Behörighet saknas.")
        _send_email(from_email, f"Re: {subject}", f"Din begäran kunde inte behandlas:\n\n{detail}")
        return
    resp.raise_for_status()
    data = resp.json()

    session_id = data["session_id"]
    confirmation_message = data["confirmation_message"]

    # [REF:{uuid}] finns redan i confirmation_message; lägg det även i subject
    reply_subject = f"Re: {subject} [REF:{session_id}]"
    _send_email(from_email, reply_subject, confirmation_message)
    logger.info(f"Nytt mejl bearbetat från {from_email}, session {session_id}")


def _process_reply(
    client: httpx.Client,
    session_id: str,
    from_email: str,
    subject: str,
    body: str,
) -> None:
    """Vidarebefordrar handläggarens svar till ai-registrator och skickar svaret."""
    payload = {"reply_text": body, "from_email": from_email}
    resp = client.post(
        f"{AI_REGISTRATOR_URL}/sessions/{session_id}/reply",
        json=payload,
        headers=_ai_headers(),
        timeout=400.0,
    )
    if resp.status_code == 403:
        detail = resp.json().get("detail", "Behörighet saknas.")
        clean_subject = re.sub(r"\s*\[REF:[^\]]+\]", "", subject).strip()
        _send_email(from_email, clean_subject, f"Din begäran kunde inte behandlas:\n\n{detail}")
        return
    if resp.status_code == 410:
        clean_subject = re.sub(r"\s*\[REF:[^\]]+\]", "", subject).strip()
        _send_email(
            from_email,
            clean_subject,
            "Din förfrågan kunde inte behandlas:\n\nSessionen har gått ut. Skicka mejlet igen för att starta en ny registrering.",
        )
        return
    resp.raise_for_status()
    data = resp.json()

    action = data["action"]
    message = data["message"]

    if action in ("updated", "unclear"):
        # Nytt förslag eller oklart svar – bevara [REF:] så nästa svar matchar rätt session
        ref_tag = f"[REF:{session_id}]"
        if ref_tag not in subject:
            reply_subject = f"{subject} {ref_tag}"
        else:
            reply_subject = subject
        if ref_tag not in message:
            message += f"\n\n{ref_tag}"
        _send_email(from_email, reply_subject, message)
    else:
        # "confirmed" eller "cancelled" – avslutande meddelande utan [REF:]
        clean_subject = re.sub(r"\s*\[REF:[^\]]+\]", "", subject).strip()
        _send_email(from_email, clean_subject, message)

    logger.info(f"Svar hanterat för session {session_id}: action={action}")


# ── Pollcykel ─────────────────────────────────────────────────────────────────

def _poll_once(imap: imapclient.IMAPClient) -> None:
    """En pollcykel: hämtar osedda mejl och bearbetar dem."""
    imap.select_folder(IMAP_FOLDER)
    uids = imap.search(["UNSEEN"])
    if not uids:
        return

    logger.info(f"Hittade {len(uids)} osedda mejl")

    with httpx.Client() as http:
        for uid in uids:
            try:
                raw = imap.fetch([uid], ["RFC822"])[uid][b"RFC822"]
                msg = email.message_from_bytes(raw)

                _, from_addr = email.utils.parseaddr(msg.get("From", ""))
                subject = _decode_header(msg.get("Subject", ""))
                body = _extract_text(msg)

                # Markera som läst omedelbart (undviker dubbelbearbetning vid krasch)
                imap.add_flags([uid], [imapclient.SEEN])

                ref_id = _find_ref(subject) or _find_ref(body)
                if ref_id:
                    _process_reply(http, ref_id, from_addr, subject, body)
                else:
                    attachments = _extract_attachments(msg)
                    _process_new_email(http, from_addr, subject, body, attachments, raw)

            except Exception as e:
                logger.error(f"Fel vid bearbetning av mejl UID {uid}: {e}", exc_info=True)


# ── Huvudloop ─────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("mail-worker startar...")

    while True:
        imap = None
        try:
            logger.info(f"Ansluter till IMAP {IMAP_HOST}:{IMAP_PORT}...")
            imap = imapclient.IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True)
            imap.login(IMAP_USER, _imap_password())
            logger.info("IMAP ansluten.")

            while True:
                _poll_once(imap)
                time.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.error(f"IMAP-fel: {e}. Återansluter om 30 s...")
        finally:
            if imap:
                try:
                    imap.logout()
                except Exception:
                    pass
        time.sleep(30)


if __name__ == "__main__":
    main()
