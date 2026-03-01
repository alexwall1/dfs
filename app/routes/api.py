import hashlib
import io
from datetime import datetime, timezone, date as date_type

from flask import request, g, send_file
from flask_smorest import Blueprint as SmorestBlueprint, abort as smorest_abort
from marshmallow import Schema, fields, validate

from app import db
from app.models import (
    Arende,
    Handling,
    DocumentVersion,
    Kategori,
    Nummerserie,
    Installning,
    APIKey,
    User,
    log_action,
)
from app.routes.handlingar import _validera_fil

blp = SmorestBlueprint(
    "api_v1",
    __name__,
    url_prefix="/api/v1",
    description="DFS2 REST API v1",
)


# ── Autentisering och behörighet ───────────────────────────────────────────────

def _check_auth(*roles):
    """
    Parsar Authorization: Bearer <nyckel>, slår upp i APIKey-tabellen och
    kontrollerar att nyckeln är aktiv och att rollen ingår i `roles` (om angivet).
    Sätter g.api_user. Anropar smorest_abort() vid fel.
    Uppdaterar anvand_senast med ett eget commit.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        smorest_abort(401, message="API-nyckel saknas i Authorization-headern.")
    raw_key = auth_header[7:].strip()
    if not raw_key:
        smorest_abort(401, message="API-nyckel är tom.")
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = APIKey.query.filter_by(key_hash=key_hash, aktiv=True).first()
    if not api_key:
        smorest_abort(401, message="Ogiltig eller inaktiv API-nyckel.")
    user = api_key.anvandare
    if not user.active or user.deleted:
        smorest_abort(401, message="Användarkontot är inaktivt eller borttaget.")
    if roles and user.role not in roles:
        smorest_abort(403, message="Otillräckliga behörigheter för denna åtgärd.")
    g.api_user = user
    api_key.anvand_senast = datetime.now(timezone.utc)
    db.session.commit()
    return user


def _sekretess_arende(arende):
    """Returnerar True om g.api_user har rätt att se ärendet."""
    if not arende.sekretess:
        return True
    user = g.api_user
    if user.role in ("admin", "registrator"):
        return True
    if user.role == "handlaggare" and arende.handlaggare_id == user.id:
        return True
    if user.role == "arkivarie" and arende.status == "arkiverat":
        return True
    return False


def _sekretess_handling(handling):
    """Returnerar True om g.api_user har rätt att se handlingen."""
    arende = handling.arende
    if not (handling.sekretess or arende.sekretess):
        return True
    user = g.api_user
    if user.role in ("admin", "registrator"):
        return True
    if user.role == "handlaggare" and arende.handlaggare_id == user.id:
        return True
    if user.role == "arkivarie" and arende.status == "arkiverat":
        return True
    return False


# ── Marshmallow-schemas ────────────────────────────────────────────────────────

class VersionUtSchema(Schema):
    id = fields.Int(dump_only=True)
    version_nr = fields.Int(dump_only=True)
    filnamn = fields.Str(dump_only=True)
    mime_type = fields.Str(dump_only=True, allow_none=True)
    kommentar = fields.Str(dump_only=True, allow_none=True)
    skapad_datum = fields.DateTime(dump_only=True)


class HandlingUtSchema(Schema):
    id = fields.Int(dump_only=True)
    arende_id = fields.Int(dump_only=True)
    typ = fields.Str(dump_only=True)
    beskrivning = fields.Str(dump_only=True)
    datum_inkom = fields.Date(dump_only=True, allow_none=True)
    avsandare = fields.Str(dump_only=True, allow_none=True)
    mottagare = fields.Str(dump_only=True, allow_none=True)
    sekretess = fields.Bool(dump_only=True)
    kategori_namn = fields.Method("_get_kategori_namn", dump_only=True)
    versioner = fields.Method("_get_versioner", dump_only=True)
    skapad_datum = fields.DateTime(dump_only=True)

    def _get_kategori_namn(self, handling):
        return [k.namn for k in handling.kategorier.all()]

    def _get_versioner(self, handling):
        schema = VersionUtSchema()
        return [
            schema.dump(v)
            for v in handling.versioner.order_by(DocumentVersion.version_nr).all()
        ]


class ArendeUtSchema(Schema):
    id = fields.Int(dump_only=True)
    diarienummer = fields.Str(dump_only=True)
    arende_mening = fields.Str(dump_only=True)
    status = fields.Str(dump_only=True)
    sekretess = fields.Bool(dump_only=True)
    sekretess_grund = fields.Str(dump_only=True, allow_none=True)
    skapad_av = fields.Method("_get_skapad_av", dump_only=True)
    handlaggare = fields.Method("_get_handlaggare", dump_only=True)
    handlaggare_id = fields.Int(dump_only=True, allow_none=True)
    skapad_datum = fields.DateTime(dump_only=True)
    andrad_datum = fields.DateTime(dump_only=True)

    def _get_skapad_av(self, arende):
        return arende.skapare.username if arende.skapare else None

    def _get_handlaggare(self, arende):
        return arende.handlaggare.username if arende.handlaggare else None


class ArendeDetaljSchema(ArendeUtSchema):
    handlingar = fields.Method("_get_handlingar", dump_only=True)

    def _get_handlingar(self, arende):
        user = g.api_user
        hs = arende.handlingar.filter_by(deleted=False).all()
        if user.role == "observator":
            if arende.sekretess:
                hs = []
            else:
                hs = [h for h in hs if not h.sekretess]
        schema = HandlingUtSchema()
        return [schema.dump(h) for h in hs]


class ArendeSkapaSchema(Schema):
    arende_mening = fields.Str(required=True, validate=validate.Length(min=1, max=500))
    sekretess = fields.Bool(load_default=False)
    sekretess_grund = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=500)
    )
    handlaggare_id = fields.Int(load_default=None, allow_none=True)
    prefix = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=20)
    )


class ArendeRedigeraSchema(Schema):
    arende_mening = fields.Str(validate=validate.Length(min=1, max=500))
    sekretess = fields.Bool()
    sekretess_grund = fields.Str(allow_none=True, validate=validate.Length(max=500))
    handlaggare_id = fields.Int(allow_none=True)


class StatusByteSchema(Schema):
    ny_status = fields.Str(required=True)


class HandlingRedigeraSchema(Schema):
    typ = fields.Str(validate=validate.OneOf(["inkommande", "utgaende", "upprattad"]))
    beskrivning = fields.Str(validate=validate.Length(min=1, max=500))
    datum_inkom = fields.Date(allow_none=True)
    avsandare = fields.Str(allow_none=True, validate=validate.Length(max=300))
    mottagare = fields.Str(allow_none=True, validate=validate.Length(max=300))
    sekretess = fields.Bool()
    kategori_ids = fields.List(fields.Int())


class PaginatedArendenSchema(Schema):
    arenden = fields.List(fields.Nested(ArendeUtSchema))
    total = fields.Int()
    sidor = fields.Int()
    sida = fields.Int()


class ArendeQuerySchema(Schema):
    status = fields.Str(load_default=None, allow_none=True)
    page = fields.Int(load_default=1, validate=validate.Range(min=1))


class BrukareUtSchema(Schema):
    id = fields.Int(dump_only=True)
    role = fields.Str(dump_only=True)
    active = fields.Bool(dump_only=True)


class BrukareQuerySchema(Schema):
    mejl = fields.Str(required=True)


# ── Ärenden ───────────────────────────────────────────────────────────────────

@blp.route("/arenden", methods=["GET"])
@blp.doc(security=[{"BearerAuth": []}])
@blp.arguments(ArendeQuerySchema, location="query")
@blp.response(200, PaginatedArendenSchema)
def lista_arenden(query_args):
    """Lista ärenden med filtrering på status och paginering."""
    from sqlalchemy import or_
    user = _check_auth()

    query = Arende.query.filter_by(deleted=False)

    status = query_args.get("status")
    if status and status in Arende.STATUS_LABELS:
        query = query.filter_by(status=status)

    # Sekretessfiltrering per roll
    if user.role == "observator":
        query = query.filter(Arende.sekretess == False)  # noqa: E712
    elif user.role == "handlaggare":
        query = query.filter(
            or_(Arende.sekretess == False, Arende.handlaggare_id == user.id)  # noqa: E712
        )
    elif user.role == "arkivarie":
        query = query.filter(
            or_(Arende.sekretess == False, Arende.status == "arkiverat")  # noqa: E712
        )

    query = query.order_by(Arende.skapad_datum.desc())
    page = query_args.get("page", 1)
    pagination = query.paginate(page=page, per_page=20, error_out=False)

    return {
        "arenden": pagination.items,
        "total": pagination.total,
        "sidor": pagination.pages,
        "sida": pagination.page,
    }


@blp.route("/arenden", methods=["POST"])
@blp.doc(security=[{"BearerAuth": []}])
@blp.arguments(ArendeSkapaSchema)
@blp.response(201, ArendeUtSchema)
def skapa_arende(body):
    """Skapa ett nytt ärende."""
    user = _check_auth("admin", "registrator")

    if user.role == "admin" and body.get("prefix"):
        prefix = body["prefix"].strip().upper()
    else:
        prefix = Installning.get("standardprefix", "DNR")

    diarienummer = Nummerserie.next_number(prefix)
    arende = Arende(
        diarienummer=diarienummer,
        arende_mening=body["arende_mening"].strip(),
        sekretess=body.get("sekretess", False),
        sekretess_grund=body.get("sekretess_grund") or None,
        skapad_av=user.id,
        handlaggare_id=body.get("handlaggare_id"),
    )
    db.session.add(arende)
    db.session.flush()
    log_action(
        user.id,
        "skapa_arende",
        "Arende",
        arende.id,
        {"diarienummer": diarienummer, "via": "api"},
    )
    db.session.commit()
    return arende


@blp.route("/arenden/<int:arende_id>", methods=["GET"])
@blp.doc(security=[{"BearerAuth": []}])
@blp.response(200, ArendeDetaljSchema)
def hamta_arende(arende_id):
    """Hämta ett ärende med alla handlingar."""
    _check_auth()

    arende = Arende.query.get_or_404(arende_id)
    if arende.deleted:
        smorest_abort(404, message="Ärendet finns inte.")
    if not _sekretess_arende(arende):
        smorest_abort(403, message="Ärendet är sekretessbelagt.")
    return arende


@blp.route("/arenden/<int:arende_id>", methods=["PUT"])
@blp.doc(security=[{"BearerAuth": []}])
@blp.arguments(ArendeRedigeraSchema)
@blp.response(200, ArendeUtSchema)
def redigera_arende(body, arende_id):
    """Redigera ett ärende."""
    user = _check_auth("admin", "registrator")

    arende = Arende.query.get_or_404(arende_id)
    if arende.deleted:
        smorest_abort(404, message="Ärendet finns inte.")

    if "arende_mening" in body:
        arende.arende_mening = body["arende_mening"].strip()
    if "sekretess" in body:
        arende.sekretess = body["sekretess"]
    if "sekretess_grund" in body:
        arende.sekretess_grund = body.get("sekretess_grund") or None
    if "handlaggare_id" in body:
        arende.handlaggare_id = body.get("handlaggare_id")

    log_action(
        user.id,
        "redigera_arende",
        "Arende",
        arende.id,
        {"diarienummer": arende.diarienummer, "via": "api"},
    )
    db.session.commit()
    return arende


@blp.route("/arenden/<int:arende_id>/status", methods=["POST"])
@blp.doc(security=[{"BearerAuth": []}])
@blp.arguments(StatusByteSchema)
@blp.response(200, ArendeUtSchema)
def byt_status(body, arende_id):
    """Byt status på ett ärende."""
    user = _check_auth("admin", "registrator", "handlaggare")

    arende = Arende.query.get_or_404(arende_id)
    if arende.deleted:
        smorest_abort(404, message="Ärendet finns inte.")

    if user.role == "handlaggare" and arende.handlaggare_id != user.id:
        smorest_abort(403, message="Du är inte tilldelad detta ärende.")

    ny_status = body["ny_status"]
    if ny_status not in arende.allowed_transitions:
        smorest_abort(
            422,
            message=(
                f"Ogiltig statusövergång från '{arende.status}' till '{ny_status}'. "
                f"Tillåtna: {arende.allowed_transitions}."
            ),
        )

    gammal = arende.status
    arende.status = ny_status
    log_action(
        user.id,
        "byt_status",
        "Arende",
        arende.id,
        {"fran": gammal, "till": ny_status, "via": "api"},
    )
    db.session.commit()
    return arende


# ── Handlingar ────────────────────────────────────────────────────────────────

@blp.route("/arenden/<int:arende_id>/handlingar", methods=["POST"])
@blp.doc(
    security=[{"BearerAuth": []}],
    requestBody={
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["typ", "beskrivning"],
                    "properties": {
                        "typ": {
                            "type": "string",
                            "enum": ["inkommande", "utgaende", "upprattad"],
                        },
                        "beskrivning": {"type": "string", "maxLength": 500},
                        "datum_inkom": {"type": "string", "format": "date"},
                        "avsandare": {"type": "string", "maxLength": 300},
                        "mottagare": {"type": "string", "maxLength": 300},
                        "sekretess": {"type": "boolean"},
                        "kategori_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                        "fil": {"type": "string", "format": "binary"},
                    },
                }
            }
        },
    },
)
@blp.response(201, HandlingUtSchema)
def skapa_handling(arende_id):
    """Skapa en ny handling på ett ärende (multipart/form-data, fil valfri)."""
    user = _check_auth("admin", "registrator", "handlaggare")

    arende = Arende.query.get_or_404(arende_id)
    if arende.deleted:
        smorest_abort(404, message="Ärendet finns inte.")

    if user.role == "handlaggare" and arende.handlaggare_id != user.id:
        smorest_abort(403, message="Du är inte tilldelad detta ärende.")

    fil = request.files.get("fil")
    fil_result = None
    if fil and fil.filename:
        try:
            fil_result = _validera_fil(fil)
        except ValueError as e:
            smorest_abort(422, message=str(e))

    typ = request.form.get("typ", "").strip()
    if typ not in ("inkommande", "utgaende", "upprattad"):
        smorest_abort(422, message="Ogiltigt värde för 'typ'.")

    beskrivning = request.form.get("beskrivning", "").strip()
    if not beskrivning:
        smorest_abort(422, message="Fältet 'beskrivning' är obligatoriskt.")

    datum_str = request.form.get("datum_inkom", "").strip()
    try:
        datum_inkom = date_type.fromisoformat(datum_str) if datum_str else date_type.today()
    except ValueError:
        smorest_abort(422, message="Ogiltigt datumformat för 'datum_inkom' (förväntas YYYY-MM-DD).")

    sekretess_raw = request.form.get("sekretess", "false").lower()
    sekretess = sekretess_raw in ("true", "1", "on")

    skapad_av_id = user.id
    if user.role in ("admin", "registrator"):
        reg_id_raw = request.form.get("registrerad_av_id", "").strip()
        if reg_id_raw:
            try:
                reg_id = int(reg_id_raw)
            except ValueError:
                smorest_abort(422, message="Ogiltigt värde för 'registrerad_av_id'.")
            reg_user = User.query.filter_by(id=reg_id, deleted=False, active=True).first()
            if not reg_user:
                smorest_abort(422, message="Användaren i 'registrerad_av_id' finns inte eller är inaktiv.")
            skapad_av_id = reg_id

    handling = Handling(
        arende_id=arende.id,
        typ=typ,
        datum_inkom=datum_inkom,
        avsandare=request.form.get("avsandare", "").strip() or None,
        mottagare=request.form.get("mottagare", "").strip() or None,
        beskrivning=beskrivning,
        sekretess=sekretess,
        skapad_av=skapad_av_id,
    )
    db.session.add(handling)
    db.session.flush()

    kategori_ids_raw = request.form.getlist("kategori_ids")
    if kategori_ids_raw:
        try:
            ids = [int(k) for k in kategori_ids_raw]
        except ValueError:
            smorest_abort(422, message="Ogiltiga kategori-id:n.")
        valda = Kategori.query.filter(Kategori.id.in_(ids)).all()
        handling.kategorier = valda

    if fil_result:
        filnamn, fildata, mime = fil_result
        version = DocumentVersion(
            handling_id=handling.id,
            version_nr=1,
            filnamn=filnamn,
            fildata=fildata,
            mime_type=mime,
            kommentar="Ursprunglig version",
            skapad_av=user.id,
        )
        db.session.add(version)

    arende.andrad_datum = datetime.now(timezone.utc)
    log_extra = {"arende": arende.diarienummer, "typ": handling.typ, "via": "api"}
    if skapad_av_id != user.id:
        log_extra["on_behalf_of"] = skapad_av_id
    log_action(user.id, "skapa_handling", "Handling", handling.id, log_extra)
    db.session.commit()
    return handling


@blp.route("/handlingar/<int:handling_id>", methods=["GET"])
@blp.doc(security=[{"BearerAuth": []}])
@blp.response(200, HandlingUtSchema)
def hamta_handling(handling_id):
    """Hämta en handling."""
    _check_auth()

    handling = Handling.query.get_or_404(handling_id)
    if handling.deleted:
        smorest_abort(404, message="Handlingen finns inte.")
    if not _sekretess_handling(handling):
        smorest_abort(403, message="Handlingen är sekretessbelagd.")
    return handling


@blp.route("/handlingar/<int:handling_id>", methods=["PUT"])
@blp.doc(security=[{"BearerAuth": []}])
@blp.arguments(HandlingRedigeraSchema)
@blp.response(200, HandlingUtSchema)
def redigera_handling(body, handling_id):
    """Redigera en handling."""
    user = _check_auth("admin", "registrator")

    handling = Handling.query.get_or_404(handling_id)
    if handling.deleted:
        smorest_abort(404, message="Handlingen finns inte.")

    if "typ" in body:
        handling.typ = body["typ"]
    if "beskrivning" in body:
        handling.beskrivning = body["beskrivning"].strip()
    if "datum_inkom" in body:
        handling.datum_inkom = body.get("datum_inkom")
    if "avsandare" in body:
        handling.avsandare = body.get("avsandare") or None
    if "mottagare" in body:
        handling.mottagare = body.get("mottagare") or None
    if "sekretess" in body:
        handling.sekretess = body["sekretess"]
    if "kategori_ids" in body:
        ids = body["kategori_ids"]
        valda = Kategori.query.filter(Kategori.id.in_(ids)).all() if ids else []
        handling.kategorier = valda

    log_action(
        user.id,
        "redigera_handling",
        "Handling",
        handling.id,
        {"arende": handling.arende.diarienummer, "via": "api"},
    )
    db.session.commit()
    return handling


@blp.route("/handlingar/<int:handling_id>/versioner", methods=["POST"])
@blp.doc(
    security=[{"BearerAuth": []}],
    requestBody={
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["fil"],
                    "properties": {
                        "fil": {"type": "string", "format": "binary"},
                        "kommentar": {"type": "string", "maxLength": 500},
                    },
                }
            }
        },
    },
)
@blp.response(201, VersionUtSchema)
def ladda_upp_version(handling_id):
    """Ladda upp en ny filversion till en handling."""
    user = _check_auth("admin", "registrator", "handlaggare")

    handling = Handling.query.get_or_404(handling_id)
    if handling.deleted:
        smorest_abort(404, message="Handlingen finns inte.")

    if user.role == "handlaggare" and handling.arende.handlaggare_id != user.id:
        smorest_abort(403, message="Du är inte tilldelad detta ärende.")

    fil = request.files.get("fil")
    if not fil or not fil.filename:
        smorest_abort(422, message="Ingen fil skickad.")

    try:
        filnamn, fildata, mime = _validera_fil(fil)
    except ValueError as e:
        smorest_abort(422, message=str(e))

    senaste = handling.versioner.order_by(DocumentVersion.version_nr.desc()).first()
    nytt_nr = (senaste.version_nr + 1) if senaste else 1

    version = DocumentVersion(
        handling_id=handling.id,
        version_nr=nytt_nr,
        filnamn=filnamn,
        fildata=fildata,
        mime_type=mime,
        kommentar=request.form.get("kommentar", "").strip() or None,
        skapad_av=user.id,
    )
    db.session.add(version)
    log_action(
        user.id,
        "ny_version",
        "DocumentVersion",
        handling.id,
        {"version": nytt_nr, "filnamn": filnamn, "via": "api"},
    )
    db.session.commit()
    return version


@blp.route("/versioner/<int:version_id>/fil", methods=["GET"])
@blp.doc(security=[{"BearerAuth": []}])
def ladda_ner_fil(version_id):
    """Ladda ned en fil (returnerar binär data)."""
    _check_auth()

    version = DocumentVersion.query.get_or_404(version_id)
    if not _sekretess_handling(version.handling):
        smorest_abort(403, message="Handlingen är sekretessbelagd.")
    return send_file(
        io.BytesIO(version.fildata),
        download_name=version.filnamn,
        mimetype=version.mime_type or "application/octet-stream",
    )


# ── Brukare ───────────────────────────────────────────────────────────────────

@blp.route("/brukare", methods=["GET"])
@blp.doc(security=[{"BearerAuth": []}])
@blp.arguments(BrukareQuerySchema, location="query")
@blp.response(200, BrukareUtSchema)
def hamta_brukare(query_args):
    """Hämta en användare via e-postadress."""
    _check_auth("admin", "registrator")

    mejl = query_args["mejl"]
    user = User.query.filter_by(email=mejl, deleted=False, active=True).first()
    if not user:
        smorest_abort(404, message="Ingen användare med den e-postadressen.")
    return user
