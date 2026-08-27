from flask import Blueprint, render_template, request, flash
from flask_login import login_required, current_user

from app.models import Arende, Kategori
from app.services import sok_arenden, _parse_datum  # noqa: F401 (re-export)

sok_bp = Blueprint("sok", __name__, url_prefix="/sok")

MAX_SOKSTRANG = 100


def _trunkera(varde: str) -> str:
    """Begränsar en sökstrung till MAX_SOKSTRANG tecken."""
    return varde[:MAX_SOKSTRANG]


@sok_bp.route("/")
@login_required
def sok():
    results = None
    q = request.args

    if any(
        q.get(k)
        for k in (
            "diarienummer",
            "mening",
            "status",
            "fran",
            "till",
            "avsandare",
            "beskrivning",
            "typ_handling",
        )
    ):
        params = {}
        if q.get("diarienummer"):
            params["diarienummer"] = _trunkera(q["diarienummer"])
        if q.get("mening"):
            params["mening"] = _trunkera(q["mening"])

        if q.get("status"):
            status_val = _trunkera(q["status"])
            if status_val not in Arende.STATUS_LABELS:
                flash(
                    f"Okänd status: '{status_val}'. Statusfiltret ignorerades.", "warning"
                )
            else:
                params["status"] = status_val

        if q.get("fran"):
            fran = _parse_datum(q["fran"])
            if fran:
                params["fran"] = fran
            else:
                flash(f"Ogiltigt datum för 'från': {q['fran']}", "warning")

        if q.get("till"):
            till = _parse_datum(q["till"])
            if till:
                params["till"] = till
            else:
                flash(f"Ogiltigt datum för 'till': {q['till']}", "warning")

        if q.get("avsandare"):
            params["avsandare"] = _trunkera(q["avsandare"])
        if q.get("beskrivning"):
            params["beskrivning"] = _trunkera(q["beskrivning"])
        if q.get("typ_handling"):
            try:
                kategori_id = int(q["typ_handling"])
            except (ValueError, TypeError):
                kategori_id = None
            if not kategori_id:
                flash(
                    "Ogiltigt värde för 'Kategori'. Filtret ignorerades.", "warning"
                )
            else:
                params["kategori_id"] = kategori_id

        results = sok_arenden(current_user, **params)

    kategorier = Kategori.query.order_by(Kategori.namn).all()
    return render_template("sok.html", results=results, q=q, kategorier=kategorier)