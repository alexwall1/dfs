import asyncio
import base64
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

import ai_client
import dfs2_client
from session_store import (
    AiSession,
    SessionLocal,
    SessionStatus,
    get_attachments_meta,
    get_conversation_history,
    get_eml_b64,
    get_proposed_handling,
    get_db,
    set_attachments_meta,
    set_conversation_history,
    set_eml_b64,
    set_proposed_handling,
)

_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=_log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "5"))


async def _cleanup_loop() -> None:
    """Raderar utgångna awaiting_reply-sessioner var 60:e sekund."""
    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            expired = (
                db.query(AiSession)
                .filter(
                    AiSession.status == SessionStatus.awaiting_reply,
                    AiSession.expires_at < now,
                )
                .all()
            )
            for s in expired:
                db.delete(s)
            if expired:
                logger.info(f"Rensade {len(expired)} utgångna session(er)")
            db.commit()
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()


app = FastAPI(title="AI-Registrator", version="1.0.0", lifespan=lifespan)
security = HTTPBearer(auto_error=False)


def _get_ai_api_key() -> str:
    try:
        with open("/run/secrets/ai_api_key") as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.environ.get("AI_API_KEY", "")


def _verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    if credentials is None:
        raise HTTPException(status_code=401, detail="API-nyckel saknas")
    expected = _get_ai_api_key()
    if not expected:
        raise HTTPException(status_code=500, detail="AI API-nyckel är inte konfigurerad på servern")
    if credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="Ogiltig API-nyckel")
    return credentials.credentials


# ── Pydantic-modeller ─────────────────────────────────────────────────────────

class Attachment(BaseModel):
    filename: str
    mime_type: str
    data_b64: str  # base64-kodad fildata


class ProcessRequest(BaseModel):
    from_email: str
    subject: str
    body_text: str
    attachments: list[Attachment] = []
    eml_b64: str | None = None  # råformat (.eml), base64-kodat


class ProcessResponse(BaseModel):
    session_id: str
    proposed_handling: dict
    confirmation_message: str


class ReplyRequest(BaseModel):
    reply_text: str
    from_email: str


class ReplyResponse(BaseModel):
    action: str  # "confirmed" | "updated" | "cancelled"
    message: str
    proposed_handling: dict | None = None
    handling_id: int | None = None


# ── Hjälpfunktioner ───────────────────────────────────────────────────────────

def _format_confirmation_message(proposed: dict, session_id: str) -> str:
    """Formaterar bekräftelsemejl till handläggaren."""
    dnr = proposed.get("diarienummer") or "okänt"
    typ = proposed.get("typ") or "okänd"
    typ_labels = {"inkommande": "Inkommande", "utgaende": "Utgående", "upprattad": "Upprättad"}
    typ_label = typ_labels.get(typ, typ)
    beskrivning = proposed.get("beskrivning") or "–"
    datum = proposed.get("datum_inkom") or "–"
    avsandare = proposed.get("avsandare") or "–"
    mottagare = proposed.get("mottagare") or "–"
    bekraftad = "Ja" if proposed.get("arende_bekraftad") else "Nej (ärendet kunde inte verifieras)"
    kommentar = proposed.get("kommentar") or ""

    lines = [
        "AI-registratorn föreslår följande diarieföring:",
        "",
        f"  Diarienummer:  {dnr} (verifierat: {bekraftad})",
        f"  Typ:           {typ_label}",
        f"  Beskrivning:   {beskrivning}",
        f"  Datum:         {datum}",
        f"  Avsändare:     {avsandare}",
        f"  Mottagare:     {mottagare}",
    ]
    if kommentar:
        lines.append(f"  Kommentar:     {kommentar}")
    lines += [
        "",
        "Svara på detta mejl med:",
        "  - 'ja' för att bekräfta och registrera handlingen",
        "  - nya instruktioner för att justera förslaget",
        "  - 'avbryt' för att avbryta",
        "",
        f"[REF:{session_id}]",
    ]
    return "\n".join(lines)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/process", response_model=ProcessResponse)
def process_email(
    req: ProcessRequest,
    _: str = Depends(_verify_api_key),
    db: Session = Depends(get_db),
):
    """Bearbetar ett inkommande e-postmeddelande och föreslår en handling."""
    # Behörighetskontroll: mejladressen måste tillhöra en aktiv DFS2-användare med rätt roll
    anvandare = dfs2_client.hamta_anvandare_via_mejl(req.from_email)
    if not anvandare:
        raise HTTPException(status_code=403, detail="Mejladressen är inte registrerad i systemet.")
    if not anvandare.get("active"):
        raise HTTPException(status_code=403, detail="Användarkontot är inaktivt.")
    if anvandare["role"] not in ("admin", "registrator", "handlaggare"):
        raise HTTPException(
            status_code=403,
            detail=f"Din roll ({anvandare['role']}) saknar behörighet att registrera handlingar.",
        )

    attachments_meta = [
        {"filename": a.filename, "mime_type": a.mime_type, "data_b64": a.data_b64}
        for a in req.attachments
    ]

    try:
        proposed = ai_client.extrahera_handling(
            email_text=req.body_text,
            from_email=req.from_email,
            subject=req.subject,
            attachments=attachments_meta,
            dfs2_get_arende_func=dfs2_client.hamta_arende_via_diarienummer,
        )
    except Exception as e:
        logger.error(f"AI extraction failed: {e}")
        raise HTTPException(status_code=500, detail=f"AI-extraktion misslyckades: {e}")

    # Kontrollera att handläggare äger ärendet redan vid process-steget
    if anvandare["role"] == "handlaggare" and proposed.get("arende_id"):
        arende = dfs2_client.hamta_arende_via_id(int(proposed["arende_id"]))
        if not arende or str(arende.get("handlaggare_id")) != str(anvandare["id"]):
            raise HTTPException(
                status_code=403,
                detail="Du är inte tilldelad handläggare för det angivna ärendet och kan inte registrera handlingar på det.",
            )

    session_id = str(uuid.uuid4())

    # Bygg initial konversationshistorik för framtida re-extraktion
    user_content = f"Från: {req.from_email}\nÄmne: {req.subject}\n\nMeddelandetext:\n{req.body_text}"
    if req.attachments:
        user_content += f"\nBilagor: {', '.join(a.filename for a in req.attachments)}"

    history = [
        {"role": "system", "content": ai_client.SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": json.dumps(proposed, ensure_ascii=False)},
    ]

    session = AiSession(
        id=session_id,
        status=SessionStatus.awaiting_reply,
        from_email=req.from_email,
        diarienummer=proposed.get("diarienummer"),
        arende_id=str(proposed["arende_id"]) if proposed.get("arende_id") else None,
        user_id=str(anvandare["id"]),
        user_role=anvandare["role"],
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=SESSION_TIMEOUT_MINUTES),
    )
    set_proposed_handling(session, proposed)
    set_conversation_history(session, history)
    set_attachments_meta(session, attachments_meta)
    set_eml_b64(session, req.eml_b64)
    db.add(session)
    db.commit()

    logger.info(f"New session {session_id} for {req.from_email}, DNR={proposed.get('diarienummer')}")
    return ProcessResponse(
        session_id=session_id,
        proposed_handling=proposed,
        confirmation_message=_format_confirmation_message(proposed, session_id),
    )


@app.post("/sessions/{session_id}/reply", response_model=ReplyResponse)
def handle_reply(
    session_id: str,
    req: ReplyRequest,
    _: str = Depends(_verify_api_key),
    db: Session = Depends(get_db),
):
    """Hanterar handläggarens svar på ett förslag."""
    session = db.get(AiSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session hittades inte")
    if session.status != SessionStatus.awaiting_reply:
        raise HTTPException(
            status_code=409,
            detail=f"Session är inte i awaiting_reply-status (är: {session.status})",
        )

    if session.expires_at and session.expires_at < datetime.utcnow():
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=410, detail="Sessionen har gått ut.")

    # Sessions-kontinuitet: avsändaren måste matcha den som startade sessionen
    if req.from_email.lower() != session.from_email.lower():
        raise HTTPException(status_code=403, detail="Mejladressen matchar inte sessionens avsändare.")

    action = ai_client.klassificera_svar(req.reply_text)
    logger.info(f"Session {session_id}: classified action={action}")

    # ── Avbryt ────────────────────────────────────────────────────────────────
    if action == "cancel":
        session.status = SessionStatus.cancelled
        session.updated_at = datetime.now(timezone.utc)
        db.commit()
        return ReplyResponse(action="cancelled", message="Åtgärden har avbrutits.")

    # ── Oklart svar ───────────────────────────────────────────────────────────
    if action == "unclear":
        return ReplyResponse(
            action="unclear",
            message="Förstod inte din instruktion. Vänligen förtydliga vad du vill göra, eller svara 'nej' för att avbryta.",
        )

    # ── Bekräfta och skapa handling ───────────────────────────────────────────
    if action == "confirm":
        proposed = get_proposed_handling(session)
        if not proposed:
            raise HTTPException(status_code=500, detail="Inget förslag lagrat i session")

        arende_id = proposed.get("arende_id")
        if not arende_id:
            session.status = SessionStatus.failed
            db.commit()
            raise HTTPException(
                status_code=422,
                detail="Inget ärende-ID i förslaget — kan inte skapa handling",
            )

        # Rollkontroll vid bekräftelse
        user_role = session.user_role or "okänd"
        if user_role not in ("admin", "registrator", "handlaggare"):
            session.status = SessionStatus.failed
            db.commit()
            raise HTTPException(status_code=403, detail="Otillräcklig behörighet för att skapa handling.")

        if user_role == "handlaggare":
            arende = dfs2_client.hamta_arende_via_id(int(arende_id))
            if not arende or str(arende.get("handlaggare_id")) != session.user_id:
                session.status = SessionStatus.failed
                db.commit()
                raise HTTPException(
                    status_code=403,
                    detail="Du är inte tilldelad handläggare för detta ärende.",
                )

        eml_b64 = get_eml_b64(session)
        fil_data = fil_namn = fil_mime = None
        if eml_b64:
            fil_data = base64.b64decode(eml_b64)
            fil_namn = "epost.eml"
            fil_mime = "message/rfc822"

        try:
            handling = dfs2_client.skapa_handling(
                arende_id=int(arende_id),
                typ=proposed.get("typ", "inkommande"),
                beskrivning=proposed.get("beskrivning", ""),
                datum_inkom=proposed.get("datum_inkom"),
                avsandare=proposed.get("avsandare"),
                mottagare=proposed.get("mottagare"),
                sekretess=proposed.get("sekretess", False),
                fil_data=fil_data,
                fil_namn=fil_namn,
                fil_mime=fil_mime,
            )
        except Exception as e:
            logger.error(f"Failed to create handling in DFS2: {e}")
            session.status = SessionStatus.failed
            db.commit()
            raise HTTPException(status_code=500, detail=f"Kunde inte skapa handling: {e}")

        session.status = SessionStatus.confirmed
        session.updated_at = datetime.now(timezone.utc)
        db.commit()

        dnr = proposed.get("diarienummer") or f"ärende {arende_id}"
        logger.info(f"Session {session_id}: handling {handling['id']} created on {dnr}")
        return ReplyResponse(
            action="confirmed",
            message=f"Handlingen har registrerats på {dnr}.",
            handling_id=handling["id"],
        )

    # ── Uppdatera med nya instruktioner ───────────────────────────────────────
    history = get_conversation_history(session)

    try:
        new_proposed = ai_client.re_extrahera_handling(
            conversation_history=history,
            new_instructions=req.reply_text,
            dfs2_get_arende_func=dfs2_client.hamta_arende_via_diarienummer,
        )
    except Exception as e:
        logger.error(f"Re-extraction failed for session {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"AI-extraktion misslyckades: {e}")

    history.append({"role": "user", "content": req.reply_text})
    history.append({"role": "assistant", "content": json.dumps(new_proposed, ensure_ascii=False)})
    set_conversation_history(session, history)
    set_proposed_handling(session, new_proposed)

    if new_proposed.get("diarienummer"):
        session.diarienummer = new_proposed["diarienummer"]
    if new_proposed.get("arende_id"):
        session.arende_id = str(new_proposed["arende_id"])
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

    confirmation_message = _format_confirmation_message(new_proposed, session_id)
    logger.info(f"Session {session_id}: updated proposal, DNR={new_proposed.get('diarienummer')}")
    return ReplyResponse(
        action="updated",
        message=confirmation_message,
        proposed_handling=new_proposed,
    )
