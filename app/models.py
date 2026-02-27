from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200))
    role = db.Column(
        db.String(20),
        nullable=False,
        default="handlaggare",
    )  # admin, registrator, handlaggare, arkivarie, observator
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    misslyckade_inloggningar = db.Column(db.Integer, default=0, nullable=False)
    last_locked_until = db.Column(db.DateTime, nullable=True)
    maste_byta_losenord = db.Column(db.Boolean, default=False, nullable=False)
    deleted = db.Column(db.Boolean, default=False, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.active

    ROLE_LABELS = {
        "admin": "Administratör",
        "registrator": "Registrator",
        "handlaggare": "Handläggare",
        "arkivarie": "Arkivarie",
        "observator": "Observatör",
    }

    @property
    def role_label(self):
        return self.ROLE_LABELS.get(self.role, self.role)


class Arende(db.Model):
    __tablename__ = "arenden"

    id = db.Column(db.Integer, primary_key=True)
    diarienummer = db.Column(db.String(30), unique=True, nullable=False)
    arende_mening = db.Column(db.String(500), nullable=False)
    status = db.Column(
        db.String(20), nullable=False, default="oppnat"
    )  # oppnat, pagaende, avslutat, arkiverat
    sekretess = db.Column(db.Boolean, default=False)
    sekretess_grund = db.Column(db.String(500))
    skapad_av = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    handlaggare_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    skapad_datum = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    andrad_datum = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted = db.Column(db.Boolean, default=False)

    skapare = db.relationship("User", foreign_keys=[skapad_av])
    handlaggare = db.relationship("User", foreign_keys=[handlaggare_id])
    handlingar = db.relationship(
        "Handling", backref="arende", lazy="dynamic", order_by="Handling.datum_inkom"
    )

    STATUS_LABELS = {
        "oppnat": "Öppnat",
        "pagaende": "Pågående",
        "avslutat": "Avslutat",
        "arkiverat": "Arkiverat",
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    STATUS_FLOW = {
        "oppnat": ["pagaende"],
        "pagaende": ["avslutat"],
        "avslutat": ["arkiverat", "pagaende"],
        "arkiverat": [],
    }

    @property
    def allowed_transitions(self):
        return self.STATUS_FLOW.get(self.status, [])


handling_kategori = db.Table(
    "handling_kategori",
    db.Column("handling_id", db.Integer, db.ForeignKey("handlingar.id"), primary_key=True),
    db.Column("kategori_id", db.Integer, db.ForeignKey("kategorier.id"), primary_key=True),
)


class Kategori(db.Model):
    __tablename__ = "kategorier"
    id = db.Column(db.Integer, primary_key=True)
    namn = db.Column(db.String(100), unique=True, nullable=False)


class Handling(db.Model):
    __tablename__ = "handlingar"

    id = db.Column(db.Integer, primary_key=True)
    arende_id = db.Column(db.Integer, db.ForeignKey("arenden.id"), nullable=False)
    typ = db.Column(
        db.String(20), nullable=False
    )  # inkommande, utgaende, upprattad
    datum_inkom = db.Column(db.Date)
    avsandare = db.Column(db.String(300))
    mottagare = db.Column(db.String(300))
    beskrivning = db.Column(db.String(500), nullable=False)
    sekretess = db.Column(db.Boolean, default=False)
    skapad_av = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    skapad_datum = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    deleted = db.Column(db.Boolean, default=False)

    skapare = db.relationship("User", foreign_keys=[skapad_av])
    kategorier = db.relationship("Kategori", secondary="handling_kategori", lazy="dynamic")
    versioner = db.relationship(
        "DocumentVersion",
        backref="handling",
        lazy="dynamic",
        order_by="DocumentVersion.version_nr",
    )

    TYP_LABELS = {
        "inkommande": "Inkommande",
        "utgaende": "Utgående",
        "upprattad": "Upprättad",
    }

    @property
    def typ_label(self):
        return self.TYP_LABELS.get(self.typ, self.typ)


class DocumentVersion(db.Model):
    __tablename__ = "document_versions"

    id = db.Column(db.Integer, primary_key=True)
    handling_id = db.Column(
        db.Integer, db.ForeignKey("handlingar.id"), nullable=False
    )
    version_nr = db.Column(db.Integer, nullable=False, default=1)
    filnamn = db.Column(db.String(300), nullable=False)
    fildata = db.Column(db.LargeBinary, nullable=False)
    mime_type = db.Column(db.String(100))
    kommentar = db.Column(db.String(500))
    skapad_av = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    skapad_datum = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    skapare = db.relationship("User", foreign_keys=[skapad_av])


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(50), nullable=False)
    target_type = db.Column(db.String(50))
    target_id = db.Column(db.Integer)
    details = db.Column(db.JSON)
    timestamp = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    ip_address = db.Column(db.String(45))

    user = db.relationship("User")


class Installning(db.Model):
    __tablename__ = "installningar"

    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(200), nullable=False)

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.get(key)
        return row.value if row else default


class Nummerserie(db.Model):
    __tablename__ = "nummerserier"

    id = db.Column(db.Integer, primary_key=True)
    prefix = db.Column(db.String(20), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    current_number = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint("prefix", "year", name="uq_prefix_year"),
    )

    @classmethod
    def next_number(cls, prefix):
        now = datetime.now(timezone.utc)
        serie = cls.query.filter_by(prefix=prefix, year=now.year).first()
        if not serie:
            serie = cls(prefix=prefix, year=now.year, current_number=0)
            db.session.add(serie)
        serie.current_number += 1
        db.session.flush()
        return f"{prefix}-{now.year}-{serie.current_number:04d}"


def validera_losenord(losenord: str) -> list[str]:
    """Returnerar en lista med felmeddelanden. Tom lista = godkänt lösenord."""
    fel = []
    if len(losenord) < 12:
        fel.append("Lösenordet måste vara minst 12 tecken långt.")
    if not any(c.isupper() for c in losenord):
        fel.append("Lösenordet måste innehålla minst en versal (A–Z).")
    if not any(c.islower() for c in losenord):
        fel.append("Lösenordet måste innehålla minst en gemen (a–z).")
    if not any(c.isdigit() for c in losenord):
        fel.append("Lösenordet måste innehålla minst en siffra (0–9).")
    if not any(c in r"""!@#$%^&*()_+-=[]{}|;':",.<>?/`~\\""" for c in losenord):
        fel.append("Lösenordet måste innehålla minst ett specialtecken (!@#$ m.fl.).")
    return fel


class APIKey(db.Model):
    __tablename__ = "api_key"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    key_hash = db.Column(db.String(64), unique=True, nullable=False)  # SHA-256 hex
    label = db.Column(db.String(100), nullable=False)
    aktiv = db.Column(db.Boolean, default=True, nullable=False)
    skapad_datum = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    anvand_senast = db.Column(db.DateTime, nullable=True)

    anvandare = db.relationship("User", backref="api_nycklar")


def log_action(user_id, action, target_type=None, target_id=None, details=None):
    from flask import request

    entry = AuditLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
        ip_address=request.remote_addr if request else None,
    )
    db.session.add(entry)
