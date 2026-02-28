import enum
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = "/data/sessions.db"
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class SessionStatus(str, enum.Enum):
    awaiting_reply = "awaiting_reply"
    confirmed = "confirmed"
    cancelled = "cancelled"
    failed = "failed"


class AiSession(Base):
    __tablename__ = "ai_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    status = Column(Enum(SessionStatus), nullable=False, default=SessionStatus.awaiting_reply)
    from_email = Column(String(500), nullable=False)
    diarienummer = Column(String(100), nullable=True)
    arende_id = Column(String(50), nullable=True)
    user_id = Column(String(50), nullable=True)    # DFS2 user.id (som sträng)
    user_role = Column(String(20), nullable=True)  # DFS2 user.role
    # JSON-encoded fields stored as text
    proposed_handling = Column(Text, nullable=True)
    conversation_history = Column(Text, nullable=True)
    attachments_meta = Column(Text, nullable=True)
    eml_b64 = Column(Text, nullable=True)  # base64-kodat råformat (.eml)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)


Base.metadata.create_all(engine)


def _migrate():
    """Lägger till nya kolumner i befintlig databas om de saknas."""
    from sqlalchemy import text
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE ai_sessions ADD COLUMN user_id TEXT",
            "ALTER TABLE ai_sessions ADD COLUMN user_role TEXT",
            "ALTER TABLE ai_sessions ADD COLUMN expires_at DATETIME",
            "ALTER TABLE ai_sessions ADD COLUMN eml_b64 TEXT",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # Kolonnen finns redan


_migrate()
SessionLocal = sessionmaker(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_proposed_handling(session: AiSession) -> dict | None:
    if session.proposed_handling:
        return json.loads(session.proposed_handling)
    return None


def set_proposed_handling(session: AiSession, value: dict) -> None:
    session.proposed_handling = json.dumps(value, ensure_ascii=False)


def get_conversation_history(session: AiSession) -> list:
    if session.conversation_history:
        return json.loads(session.conversation_history)
    return []


def set_conversation_history(session: AiSession, value: list) -> None:
    session.conversation_history = json.dumps(value, ensure_ascii=False)


def get_attachments_meta(session: AiSession) -> list:
    if session.attachments_meta:
        return json.loads(session.attachments_meta)
    return []


def set_attachments_meta(session: AiSession, value: list) -> None:
    session.attachments_meta = json.dumps(value, ensure_ascii=False)


def get_eml_b64(session: AiSession) -> str | None:
    return session.eml_b64


def set_eml_b64(session: AiSession, value: str | None) -> None:
    session.eml_b64 = value
