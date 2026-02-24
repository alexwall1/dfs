from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app import db
from app.models import Arende, User, Nummerserie, log_action
from app.auth import role_required

arenden_bp = Blueprint("arenden", __name__, url_prefix="/arenden")


@arenden_bp.route("/")
@login_required
def lista():
    page = request.args.get("page", 1, type=int)
    query = Arende.query.filter_by(deleted=False)

    status = request.args.get("status")
    if status and status in Arende.STATUS_LABELS:
        query = query.filter_by(status=status)
    elif status:
        status = None  # ogiltigt värde — ignorera utan filter

    query = query.order_by(Arende.skapad_datum.desc())
    pagination = query.paginate(page=page, per_page=20, error_out=False)
    return render_template("arenden/lista.html", pagination=pagination, status=status)


@arenden_bp.route("/ny", methods=["GET", "POST"])
@role_required("admin", "registrator")
def ny():
    if request.method == "POST":
        prefix = request.form.get("prefix", "DNR").strip().upper() or "DNR"
        diarienummer = Nummerserie.next_number(prefix)

        arende = Arende(
            diarienummer=diarienummer,
            arende_mening=request.form["arende_mening"].strip(),
            sekretess="sekretess" in request.form,
            sekretess_grund=request.form.get("sekretess_grund", "").strip() or None,
            skapad_av=current_user.id,
            handlaggare_id=request.form.get("handlaggare_id", type=int) or None,
        )
        db.session.add(arende)
        db.session.flush()
        log_action(
            current_user.id,
            "skapa_arende",
            "Arende",
            arende.id,
            {"diarienummer": diarienummer},
        )
        db.session.commit()
        flash(f"Ärende {diarienummer} skapat.", "success")
        return redirect(url_for("arenden.visa", arende_id=arende.id))

    handlaggare = User.query.filter_by(active=True).order_by(User.full_name).all()
    return render_template("arenden/ny.html", handlaggare=handlaggare)


@arenden_bp.route("/<int:arende_id>")
@login_required
def visa(arende_id):
    arende = Arende.query.get_or_404(arende_id)
    if arende.deleted:
        flash("Ärendet finns inte.", "danger")
        return redirect(url_for("arenden.lista"))

    handlingar = arende.handlingar.filter_by(deleted=False).all()
    return render_template("arenden/visa.html", arende=arende, handlingar=handlingar)


@arenden_bp.route("/<int:arende_id>/redigera", methods=["GET", "POST"])
@role_required("admin", "registrator")
def redigera(arende_id):
    arende = Arende.query.get_or_404(arende_id)

    if request.method == "POST":
        arende.arende_mening = request.form["arende_mening"].strip()
        arende.sekretess = "sekretess" in request.form
        arende.sekretess_grund = (
            request.form.get("sekretess_grund", "").strip() or None
        )
        arende.handlaggare_id = request.form.get("handlaggare_id", type=int) or None
        log_action(
            current_user.id,
            "redigera_arende",
            "Arende",
            arende.id,
            {"diarienummer": arende.diarienummer},
        )
        db.session.commit()
        flash("Ärendet uppdaterat.", "success")
        return redirect(url_for("arenden.visa", arende_id=arende.id))

    handlaggare = User.query.filter_by(active=True).order_by(User.full_name).all()
    return render_template(
        "arenden/redigera.html", arende=arende, handlaggare=handlaggare
    )


@arenden_bp.route("/<int:arende_id>/status", methods=["POST"])
@role_required("admin", "registrator", "handlaggare")
def byt_status(arende_id):
    arende = Arende.query.get_or_404(arende_id)
    ny_status = request.form.get("ny_status")

    if ny_status not in arende.allowed_transitions:
        flash("Ogiltig statusövergång.", "danger")
        return redirect(url_for("arenden.visa", arende_id=arende.id))

    gammal = arende.status
    arende.status = ny_status
    log_action(
        current_user.id,
        "byt_status",
        "Arende",
        arende.id,
        {"fran": gammal, "till": ny_status},
    )
    db.session.commit()
    flash(f"Status ändrad till {arende.status_label}.", "success")
    return redirect(url_for("arenden.visa", arende_id=arende.id))


@arenden_bp.route("/<int:arende_id>/ta-bort", methods=["POST"])
@role_required("admin")
def ta_bort(arende_id):
    arende = Arende.query.get_or_404(arende_id)
    arende.deleted = True
    log_action(
        current_user.id,
        "ta_bort_arende",
        "Arende",
        arende.id,
        {"diarienummer": arende.diarienummer},
    )
    db.session.commit()
    flash("Ärendet borttaget.", "success")
    return redirect(url_for("arenden.lista"))
