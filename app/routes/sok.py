from datetime import date

from flask import Blueprint, render_template, request, flash
from flask_login import login_required

from app.models import Arende, Handling

sok_bp = Blueprint("sok", __name__, url_prefix="/sok")

MAX_SOKSTRANG = 100


def _parse_datum(varde: str) -> date | None:
    """Parsar ett ISO-datumsträng säkert. Returnerar None vid ogiltigt format."""
    try:
        return date.fromisoformat(varde)
    except (ValueError, TypeError):
        return None


def _trunkera(varde: str) -> str:
    """Begränsar en söksträng till MAX_SOKSTRANG tecken."""
    return varde[:MAX_SOKSTRANG]


@sok_bp.route("/")
@login_required
def sok():
    results = None
    q = request.args

    if any(q.get(k) for k in ("diarienummer", "mening", "status", "fran", "till", "avsandare")):
        query = Arende.query.filter_by(deleted=False)

        if q.get("diarienummer"):
            query = query.filter(
                Arende.diarienummer.ilike(f"%{_trunkera(q['diarienummer'])}%")
            )
        if q.get("mening"):
            query = query.filter(
                Arende.arende_mening.ilike(f"%{_trunkera(q['mening'])}%")
            )
        if q.get("status"):
            status_val = _trunkera(q["status"])
            if status_val not in Arende.STATUS_LABELS:
                flash(f"Okänd status: '{status_val}'. Statusfiltret ignorerades.", "warning")
            else:
                query = query.filter_by(status=status_val)

        if q.get("fran"):
            fran = _parse_datum(q["fran"])
            if fran:
                query = query.filter(Arende.skapad_datum >= fran)
            else:
                flash(f"Ogiltigt datum för 'från': {q['fran']}", "warning")

        if q.get("till"):
            till = _parse_datum(q["till"])
            if till:
                query = query.filter(Arende.skapad_datum <= till)
            else:
                flash(f"Ogiltigt datum för 'till': {q['till']}", "warning")

        if q.get("avsandare"):
            arende_ids = (
                Handling.query.filter(
                    Handling.avsandare.ilike(f"%{_trunkera(q['avsandare'])}%"),
                    Handling.deleted == False,
                )
                .with_entities(Handling.arende_id)
                .distinct()
            )
            query = query.filter(Arende.id.in_(arende_ids))

        results = query.order_by(Arende.skapad_datum.desc()).limit(100).all()

    return render_template("sok.html", results=results, q=q)
