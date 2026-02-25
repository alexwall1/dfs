from datetime import date
import io
import magic
from werkzeug.utils import secure_filename

from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    flash,
    request,
    send_file,
    abort,
)
from flask_login import login_required, current_user

from flask import current_app

from app import db
from app.models import Arende, Handling, DocumentVersion, TypAvHandling, log_action
from app.auth import role_required


def _max_fil_storlek_bytes() -> int:
    return current_app.config["MAX_FIL_STORLEK_MB"] * 1024 * 1024

# Tillåtna filändelser mappade till godkända MIME-typer.
# DOCX och XLSX är ZIP-baserade format — äldre libmagic-versioner
# returnerar application/zip, nyare returnerar den specifika OOXML-typen.
TILLÅTNA_FILTYPER: dict[str, set[str]] = {
    "pdf": {"application/pdf"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
    },
    "png": {"image/png"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
}


def _validera_fil(fil) -> tuple[str, bytes, str]:
    """
    Validerar och sanerar en uppladdad fil.
    Returnerar (säkert_filnamn, fildata, mime_typ) om filen är godkänd.
    Kastar ValueError med ett svenskt felmeddelande annars.
    """
    filnamn = secure_filename(fil.filename or "")
    if not filnamn:
        raise ValueError("Ogiltigt eller tomt filnamn.")

    delar = filnamn.rsplit(".", 1)
    if len(delar) < 2:
        raise ValueError("Filen saknar filändelse.")
    andelse = delar[1].lower()

    if andelse not in TILLÅTNA_FILTYPER:
        tillåtna = ", ".join(sorted(TILLÅTNA_FILTYPER))
        raise ValueError(
            f"Filtypen .{andelse} är inte tillåten. Tillåtna filtyper: {tillåtna}."
        )

    fildata = fil.read()
    max_bytes = _max_fil_storlek_bytes()
    if len(fildata) > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise ValueError(f"Filen är för stor. Maximal filstorlek är {max_mb} MB.")

    detekterad_mime = magic.from_buffer(fildata, mime=True)
    if detekterad_mime not in TILLÅTNA_FILTYPER[andelse]:
        raise ValueError(
            f"Filens innehåll ({detekterad_mime}) stämmer inte med filändelsen .{andelse}."
        )

    return filnamn, fildata, detekterad_mime


def _har_sekretessbehorighet(handling):
    """Kontrollera om current_user har rätt att se en sekretessbelagd handling."""
    arende = handling.arende
    if not (handling.sekretess or arende.sekretess):
        return True
    if current_user.role in ("admin", "registrator"):
        return True
    if current_user.role == "handlaggare" and arende.handlaggare_id == current_user.id:
        return True
    if current_user.role == "arkivarie" and arende.status == "arkiverat":
        return True
    return False

handlingar_bp = Blueprint("handlingar", __name__, url_prefix="/handlingar")


@handlingar_bp.route("/ny/<int:arende_id>", methods=["GET", "POST"])
@role_required("admin", "registrator", "handlaggare")
def ny(arende_id):
    arende = Arende.query.get_or_404(arende_id)

    if request.method == "POST":
        # Validera filen innan vi skapar handling-posten för att undvika
        # att en ogiltig fil lämnar en halvt skapad post i databasen.
        fil = request.files.get("fil")
        fil_result = None
        if fil and fil.filename:
            try:
                fil_result = _validera_fil(fil)
            except ValueError as e:
                flash(str(e), "danger")
                typer = TypAvHandling.query.order_by(TypAvHandling.namn).all()
                return render_template("handlingar/ny.html", arende=arende, typer=typer)

        datum_str = request.form.get("datum_inkom")
        datum_inkom = date.fromisoformat(datum_str) if datum_str else date.today()

        handling = Handling(
            arende_id=arende.id,
            typ=request.form["typ"],
            datum_inkom=datum_inkom,
            avsandare=request.form.get("avsandare", "").strip() or None,
            mottagare=request.form.get("mottagare", "").strip() or None,
            beskrivning=request.form["beskrivning"].strip(),
            sekretess="sekretess" in request.form,
            skapad_av=current_user.id,
        )
        db.session.add(handling)
        db.session.flush()

        typ_ids = request.form.getlist("typer")
        if typ_ids:
            valda_typer = TypAvHandling.query.filter(
                TypAvHandling.id.in_([int(t) for t in typ_ids if t.isdigit()])
            ).all()
            handling.typer = valda_typer

        if fil_result:
            filnamn, fildata, mime = fil_result
            version = DocumentVersion(
                handling_id=handling.id,
                version_nr=1,
                filnamn=filnamn,
                fildata=fildata,
                mime_type=mime,
                kommentar="Ursprunglig version",
                skapad_av=current_user.id,
            )
            db.session.add(version)

        log_action(
            current_user.id,
            "skapa_handling",
            "Handling",
            handling.id,
            {"arende": arende.diarienummer, "typ": handling.typ},
        )
        db.session.commit()
        flash("Handling registrerad.", "success")
        return redirect(url_for("arenden.visa", arende_id=arende.id))

    typer = TypAvHandling.query.order_by(TypAvHandling.namn).all()
    return render_template("handlingar/ny.html", arende=arende, typer=typer)


@handlingar_bp.route("/<int:handling_id>")
@login_required
def visa(handling_id):
    handling = Handling.query.get_or_404(handling_id)
    if not _har_sekretessbehorighet(handling):
        abort(403)
    versioner = handling.versioner.all()
    return render_template(
        "handlingar/visa.html", handling=handling, versioner=versioner
    )


@handlingar_bp.route("/<int:handling_id>/ny-version", methods=["POST"])
@role_required("admin", "registrator", "handlaggare")
def ny_version(handling_id):
    handling = Handling.query.get_or_404(handling_id)
    fil = request.files.get("fil")

    if not fil or not fil.filename:
        flash("Ingen fil vald.", "danger")
        return redirect(url_for("handlingar.visa", handling_id=handling.id))

    try:
        filnamn, fildata, mime = _validera_fil(fil)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("handlingar.visa", handling_id=handling.id))

    senaste = (
        handling.versioner.order_by(DocumentVersion.version_nr.desc()).first()
    )
    nytt_nr = (senaste.version_nr + 1) if senaste else 1

    version = DocumentVersion(
        handling_id=handling.id,
        version_nr=nytt_nr,
        filnamn=filnamn,
        fildata=fildata,
        mime_type=mime,
        kommentar=request.form.get("kommentar", "").strip() or None,
        skapad_av=current_user.id,
    )
    db.session.add(version)
    log_action(
        current_user.id,
        "ny_version",
        "DocumentVersion",
        handling.id,
        {"version": nytt_nr, "filnamn": filnamn},
    )
    db.session.commit()
    flash(f"Version {nytt_nr} uppladdad.", "success")
    return redirect(url_for("handlingar.visa", handling_id=handling.id))


@handlingar_bp.route("/ladda-ner/<int:version_id>")
@login_required
def ladda_ner(version_id):
    version = DocumentVersion.query.get_or_404(version_id)
    if not _har_sekretessbehorighet(version.handling):
        abort(403)
    return send_file(
        io.BytesIO(version.fildata),
        download_name=version.filnamn,
        mimetype=version.mime_type or "application/octet-stream",
    )


@handlingar_bp.route("/<int:handling_id>/ta-bort", methods=["POST"])
@role_required("admin", "registrator")
def ta_bort(handling_id):
    handling = Handling.query.get_or_404(handling_id)
    arende_id = handling.arende_id
    handling.deleted = True
    log_action(
        current_user.id,
        "ta_bort_handling",
        "Handling",
        handling.id,
        {"arende_id": arende_id},
    )
    db.session.commit()
    flash("Handling borttagen.", "success")
    return redirect(url_for("arenden.visa", arende_id=arende_id))
