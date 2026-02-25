from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import current_user

from app import db
from app.models import User, AuditLog, Nummerserie, log_action, validera_losenord
from app.auth import role_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
@role_required("admin")
def index():
    return redirect(url_for("admin.anvandare"))


@admin_bp.route("/anvandare")
@role_required("admin")
def anvandare():
    users = User.query.filter_by(deleted=False).order_by(User.full_name).all()
    return render_template("admin/anvandare.html", users=users)


@admin_bp.route("/anvandare/ny", methods=["GET", "POST"])
@role_required("admin")
def ny_anvandare():
    if request.method == "POST":
        username = request.form["username"].strip()
        if User.query.filter_by(username=username).first():
            flash("Användarnamnet är redan taget.", "danger")
            return render_template("admin/ny_anvandare.html")

        losenord = request.form.get("password", "")
        fel = validera_losenord(losenord)
        if fel:
            for msg in fel:
                flash(msg, "danger")
            return render_template("admin/ny_anvandare.html")

        user = User(
            username=username,
            full_name=request.form["full_name"].strip(),
            email=request.form.get("email", "").strip() or None,
            role=request.form["role"],
            maste_byta_losenord="maste_byta_losenord" in request.form,
        )
        user.set_password(losenord)
        db.session.add(user)
        db.session.flush()
        log_action(
            current_user.id,
            "skapa_anvandare",
            "User",
            user.id,
            {"username": username, "role": user.role},
        )
        db.session.commit()
        flash(f"Användare {username} skapad.", "success")
        return redirect(url_for("admin.anvandare"))

    return render_template("admin/ny_anvandare.html")


@admin_bp.route("/anvandare/<int:user_id>/ta-bort", methods=["POST"])
@role_required("admin")
def ta_bort_anvandare(user_id):
    user = User.query.get_or_404(user_id)

    if user.deleted:
        flash("Användaren är redan borttagen.", "danger")
        return redirect(url_for("admin.anvandare"))

    if user.id == current_user.id:
        flash("Du kan inte ta bort ditt eget konto.", "danger")
        return redirect(url_for("admin.redigera_anvandare", user_id=user_id))

    if user.role == "admin":
        kvarvarande = User.query.filter(
            User.role == "admin",
            User.active == True,
            User.deleted == False,
            User.id != user_id,
        ).count()
        if kvarvarande == 0:
            flash("Det måste finnas minst en aktiv administratör.", "danger")
            return redirect(url_for("admin.redigera_anvandare", user_id=user_id))

    user.deleted = True
    user.active = False
    log_action(
        current_user.id,
        "ta_bort_anvandare",
        "User",
        user.id,
        {"username": user.username, "role": user.role},
    )
    db.session.commit()
    flash(f"Användare {user.username} borttagen.", "success")
    return redirect(url_for("admin.anvandare"))


@admin_bp.route("/anvandare/<int:user_id>/redigera", methods=["GET", "POST"])
@role_required("admin")
def redigera_anvandare(user_id):
    user = User.query.get_or_404(user_id)

    if user.deleted:
        flash("Användaren är borttagen.", "danger")
        return redirect(url_for("admin.anvandare"))

    if request.method == "POST":
        user.full_name = request.form["full_name"].strip()
        user.email = request.form.get("email", "").strip() or None
        user.role = request.form["role"]
        user.active = "active" in request.form
        user.maste_byta_losenord = "maste_byta_losenord" in request.form

        password = request.form.get("password", "").strip()
        if password:
            fel = validera_losenord(password)
            if fel:
                for msg in fel:
                    flash(msg, "danger")
                return render_template("admin/redigera_anvandare.html", user=user)
            user.set_password(password)

        log_action(
            current_user.id,
            "redigera_anvandare",
            "User",
            user.id,
            {"username": user.username},
        )
        db.session.commit()
        flash("Användare uppdaterad.", "success")
        return redirect(url_for("admin.anvandare"))

    return render_template("admin/redigera_anvandare.html", user=user)


@admin_bp.route("/nummerserier")
@role_required("admin")
def nummerserier():
    serier = Nummerserie.query.order_by(
        Nummerserie.year.desc(), Nummerserie.prefix
    ).all()
    return render_template("admin/nummerserier.html", serier=serier)


@admin_bp.route("/logg")
@role_required("admin")
def logg():
    page = request.args.get("page", 1, type=int)
    pagination = (
        AuditLog.query.order_by(AuditLog.timestamp.desc())
        .paginate(page=page, per_page=50, error_out=False)
    )
    return render_template("admin/logg.html", pagination=pagination)
