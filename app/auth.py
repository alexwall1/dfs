from functools import wraps
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from app import db, limiter
from app.models import User, log_action, validera_losenord

MAX_MISSLYCKADE_FORSOK = 5
LASNINGSTID_MINUTER = 15

auth_bp = Blueprint("auth", __name__)

# Endpoints som är tillåtna även när lösenordsbyte krävs.
_UNDANTAGNA_ENDPOINTS = {"auth.byt_losenord", "auth.logout", "static"}


@auth_bp.before_app_request
def kontrollera_losenordsbyte():
    if (
        current_user.is_authenticated
        and current_user.maste_byta_losenord
        and request.endpoint not in _UNDANTAGNA_ENDPOINTS
    ):
        flash("Du måste byta lösenord innan du kan fortsätta.", "warning")
        return redirect(url_for("auth.byt_losenord"))


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                log_action(
                    current_user.id,
                    "behorighet_nekad",
                    details={
                        "endpoint": request.endpoint,
                        "metod": request.method,
                        "kravda_roller": list(roles),
                        "roll": current_user.role,
                    },
                )
                db.session.commit()
                flash("Du har inte behörighet för denna sida.", "danger")
                return redirect(url_for("auth.dashboard"))
            return f(*args, **kwargs)
        return wrapped
    return decorator


@auth_bp.route("/")
@login_required
def index():
    return redirect(url_for("auth.dashboard"))


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("auth.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user and user.active:
            nu = datetime.now(timezone.utc)

            if user.last_locked_until and user.last_locked_until.replace(tzinfo=timezone.utc) > nu:
                sekunder_kvar = int((user.last_locked_until.replace(tzinfo=timezone.utc) - nu).total_seconds())
                minuter_kvar = max(1, (sekunder_kvar + 59) // 60)
                flash(
                    f"Kontot är tillfälligt låst efter för många misslyckade inloggningar. "
                    f"Försök igen om {minuter_kvar} minut(er).",
                    "danger",
                )
                return render_template("login.html")

            if user.check_password(password):
                user.misslyckade_inloggningar = 0
                user.last_locked_until = None
                login_user(user)
                log_action(user.id, "login")
                db.session.commit()
                next_page = request.args.get("next")
                if next_page:
                    parsed = urlparse(next_page)
                    if parsed.netloc or parsed.scheme:
                        next_page = None
                return redirect(next_page or url_for("auth.dashboard"))
            else:
                user.misslyckade_inloggningar = (user.misslyckade_inloggningar or 0) + 1
                log_action(
                    user.id,
                    "misslyckad_inloggning",
                    details={"forsok": user.misslyckade_inloggningar},
                )
                if user.misslyckade_inloggningar >= MAX_MISSLYCKADE_FORSOK:
                    user.last_locked_until = datetime.now(timezone.utc) + timedelta(minutes=LASNINGSTID_MINUTER)
                    log_action(user.id, "konto_last", details={"misslyckade_forsok": user.misslyckade_inloggningar})
                db.session.commit()
        else:
            # Felaktigt användarnamn — logga utan user_id för att inte läcka info
            log_action(
                None,
                "misslyckad_inloggning",
                details={"username": username},
            )
            db.session.commit()

        flash("Felaktigt användarnamn eller lösenord.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    log_action(current_user.id, "logout")
    db.session.commit()
    logout_user()
    flash("Du har loggats ut.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/dashboard")
@login_required
def dashboard():
    from app.models import Arende, Handling
    from sqlalchemy import or_

    stats = {
        "oppna": Arende.query.filter_by(status="oppnat", deleted=False).count(),
        "pagaende": Arende.query.filter_by(status="pagaende", deleted=False).count(),
        "avslutade": Arende.query.filter_by(status="avslutat", deleted=False).count(),
    }

    mina_arenden = []
    if current_user.role == "handlaggare":
        mina_arenden = (
            Arende.query.filter_by(
                handlaggare_id=current_user.id, deleted=False
            )
            .filter(Arende.status.in_(["oppnat", "pagaende"]))
            .order_by(Arende.andrad_datum.desc())
            .limit(10)
            .all()
        )

    senaste = (
        Arende.query.filter_by(deleted=False)
        .order_by(Arende.skapad_datum.desc())
        .limit(10)
        .all()
    )

    sokresultat = None
    q = request.args.get("q", "").strip()
    if q:
        qt = q[:100]
        base = Arende.query.filter_by(deleted=False)

        if current_user.role in ("admin", "registrator"):
            handling_ids = (
                Handling.query.filter(
                    Handling.beskrivning.ilike(f"%{qt}%"),
                    Handling.deleted == False,
                )
                .with_entities(Handling.arende_id)
                .distinct()
            )
            sokresultat = (
                base.filter(
                    or_(
                        Arende.arende_mening.ilike(f"%{qt}%"),
                        Arende.id.in_(handling_ids),
                    )
                )
                .order_by(Arende.skapad_datum.desc())
                .limit(50)
                .all()
            )
        elif current_user.role == "handlaggare":
            agda_ids = (
                Arende.query.filter_by(
                    handlaggare_id=current_user.id, deleted=False
                ).with_entities(Arende.id)
            )
            handling_ids = (
                Handling.query.filter(
                    Handling.beskrivning.ilike(f"%{qt}%"),
                    Handling.deleted == False,
                    Handling.arende_id.in_(agda_ids),
                )
                .with_entities(Handling.arende_id)
                .distinct()
            )
            sokresultat = (
                base.filter(
                    or_(
                        Arende.arende_mening.ilike(f"%{qt}%"),
                        Arende.id.in_(handling_ids),
                    )
                )
                .order_by(Arende.skapad_datum.desc())
                .limit(50)
                .all()
            )
        else:
            if current_user.role == "observator":
                base = base.filter(Arende.sekretess == False)
            sokresultat = (
                base.filter(Arende.arende_mening.ilike(f"%{qt}%"))
                .order_by(Arende.skapad_datum.desc())
                .limit(50)
                .all()
            )

    return render_template(
        "dashboard.html",
        stats=stats,
        mina_arenden=mina_arenden,
        senaste=senaste,
        sokresultat=sokresultat,
        q=q,
    )


@auth_bp.route("/byt-losenord", methods=["GET", "POST"])
@login_required
def byt_losenord():
    if request.method == "POST":
        nytt = request.form.get("nytt_losenord", "")
        bekraftelse = request.form.get("bekraftelse", "")

        if nytt != bekraftelse:
            flash("Lösenorden matchar inte.", "danger")
            return render_template("byt_losenord.html")

        fel = validera_losenord(nytt)
        if fel:
            for msg in fel:
                flash(msg, "danger")
            return render_template("byt_losenord.html")

        current_user.set_password(nytt)
        current_user.maste_byta_losenord = False
        log_action(current_user.id, "byta_losenord")
        db.session.commit()
        flash("Lösenordet har bytts.", "success")
        return redirect(url_for("auth.dashboard"))

    return render_template("byt_losenord.html")


@auth_bp.route("/hjalp")
@login_required
def hjalp():
    return render_template("hjalp.html")
