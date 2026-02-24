import json
import re
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, Response
from flask_login import current_user

from app import db
from app.models import Arende, AuditLog, log_action
from app.auth import role_required

# Matchar null-bytes och oprintbara kontrollkaraktärer (utom tab/LF/CR).
_KONTROLLKARTECKEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanera_exportvarde(varde):
    """Tar bort null-bytes och kontrollkaraktärer ur strängvärden i exportdata."""
    if isinstance(varde, str):
        return _KONTROLLKARTECKEN.sub("", varde)
    if isinstance(varde, dict):
        return {k: _sanera_exportvarde(v) for k, v in varde.items()}
    if isinstance(varde, list):
        return [_sanera_exportvarde(v) for v in varde]
    return varde

arkiv_bp = Blueprint("arkiv", __name__, url_prefix="/arkiv")


@arkiv_bp.route("/")
@role_required("admin", "arkivarie")
def index():
    arenden = (
        Arende.query.filter(
            Arende.status.in_(["avslutat", "arkiverat"]),
            Arende.deleted == False,
        )
        .order_by(Arende.andrad_datum.desc())
        .all()
    )
    return render_template("arkiv/index.html", arenden=arenden)


@arkiv_bp.route("/exportera/<int:arende_id>")
@role_required("admin", "arkivarie")
def exportera(arende_id):
    arende = Arende.query.get_or_404(arende_id)

    handlingar_data = []
    for h in arende.handlingar.filter_by(deleted=False).all():
        versioner = [
            {
                "version_nr": v.version_nr,
                "filnamn": v.filnamn,
                "mime_type": v.mime_type,
                "kommentar": v.kommentar,
                "skapad_av": v.skapare.full_name if v.skapare else None,
                "skapad_datum": v.skapad_datum.isoformat() if v.skapad_datum else None,
            }
            for v in h.versioner.all()
        ]
        handlingar_data.append(
            {
                "id": h.id,
                "typ": h.typ,
                "datum_inkom": h.datum_inkom.isoformat() if h.datum_inkom else None,
                "avsandare": h.avsandare,
                "mottagare": h.mottagare,
                "beskrivning": h.beskrivning,
                "sekretess": h.sekretess,
                "versioner": versioner,
            }
        )

    audit_entries = (
        AuditLog.query.filter_by(target_type="Arende", target_id=arende.id)
        .order_by(AuditLog.timestamp)
        .all()
    )
    logg_data = [
        _sanera_exportvarde(
            {
                "action": e.action,
                "user": e.user.full_name if e.user else None,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "details": e.details,
            }
        )
        for e in audit_entries
    ]

    export = {
        "diarienummer": arende.diarienummer,
        "arende_mening": arende.arende_mening,
        "status": arende.status,
        "sekretess": arende.sekretess,
        "sekretess_grund": arende.sekretess_grund,
        "skapad_av": arende.skapare.full_name if arende.skapare else None,
        "handlaggare": arende.handlaggare.full_name if arende.handlaggare else None,
        "skapad_datum": arende.skapad_datum.isoformat() if arende.skapad_datum else None,
        "handlingar": handlingar_data,
        "audit_log": logg_data,
        "exporterad": datetime.now(timezone.utc).isoformat(),
    }

    log_action(
        current_user.id,
        "exportera_arende",
        "Arende",
        arende.id,
        {"diarienummer": arende.diarienummer},
    )
    db.session.commit()

    return Response(
        json.dumps(export, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={
            "Content-Disposition": f"attachment; filename={arende.diarienummer}.json"
        },
    )
