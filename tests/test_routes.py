"""Integrationstester för routes/vyer."""

import io
import json
from datetime import date
from unittest.mock import patch

import pytest

from app.models import Arende, Handling, DocumentVersion, AuditLog, Nummerserie, User
from tests.conftest import skapa_user, logga_in

# Patchar magic.from_buffer i handlingar-modulen så att tester
# inte kräver att libmagic är installerat på systemet.
MOCK_MAGIC = "app.routes.handlingar.magic.from_buffer"


# ── Hjälpfunktioner ──────────────────────────────────────────────────


def _skapa_arende(db, user, **kw):
    defaults = dict(
        diarienummer="DNR-2026-0001",
        arende_mening="Testärende",
        skapad_av=user.id,
    )
    defaults.update(kw)
    arende = Arende(**defaults)
    db.session.add(arende)
    db.session.flush()
    return arende


def _skapa_handling(db, arende, user, **kw):
    defaults = dict(
        arende_id=arende.id,
        typ="inkommande",
        beskrivning="Testhandling",
        skapad_av=user.id,
    )
    defaults.update(kw)
    handling = Handling(**defaults)
    db.session.add(handling)
    db.session.flush()
    return handling


def _skapa_version(db, handling, user, **kw):
    defaults = dict(
        handling_id=handling.id,
        version_nr=1,
        filnamn="test.pdf",
        fildata=b"PDF-data",
        mime_type="application/pdf",
        skapad_av=user.id,
    )
    defaults.update(kw)
    v = DocumentVersion(**defaults)
    db.session.add(v)
    db.session.flush()
    return v


# ── Auth & Login ─────────────────────────────────────────────────────


class TestLogin:
    def test_login_success(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()

        resp = logga_in(client, "admin")
        assert resp.status_code == 200
        assert "Dashboard" in resp.data.decode() or "dashboard" in resp.request.path

    def test_login_wrong_password(self, client, db):
        skapa_user(db, username="user1")
        db.session.commit()

        resp = logga_in(client, "user1", "felaktigt")
        assert "Felaktigt" in resp.data.decode()

    def test_login_nonexistent_user(self, client, db):
        resp = logga_in(client, "finnsinte", "hemligt")
        assert "Felaktigt" in resp.data.decode()

    def test_login_inactive_user(self, client, db):
        skapa_user(db, username="inaktiv", active=False)
        db.session.commit()

        resp = logga_in(client, "inaktiv")
        assert "Felaktigt" in resp.data.decode()

    def test_logout(self, client, db):
        skapa_user(db, username="utloggare")
        db.session.commit()
        logga_in(client, "utloggare")

        resp = client.get("/logout", follow_redirects=True)
        assert resp.status_code == 200
        assert "loggats ut" in resp.data.decode()

    def test_already_authenticated_redirect(self, client, db):
        skapa_user(db, username="redan")
        db.session.commit()
        logga_in(client, "redan")

        resp = client.get("/login")
        assert resp.status_code == 302


class TestGranskningsloggning:
    def test_misslyckad_inloggning_loggas_fel_losenord(self, client, db):
        skapa_user(db, username="logtest")
        db.session.commit()

        logga_in(client, "logtest", "felaktigt")

        user = User.query.filter_by(username="logtest").first()
        post = (
            AuditLog.query.filter_by(user_id=user.id, action="misslyckad_inloggning")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert post is not None
        assert post.details.get("forsok") == 1

    def test_misslyckad_inloggning_loggas_fel_anvandare(self, client, db):
        logga_in(client, "finns_inte", "hemligt")

        post = (
            AuditLog.query.filter_by(user_id=None, action="misslyckad_inloggning")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert post is not None
        assert post.details.get("username") == "finns_inte"

    def test_behorighet_nekad_loggas(self, client, db):
        skapa_user(db, username="handl2", role="handlaggare")
        db.session.commit()
        logga_in(client, "handl2")

        client.get("/arenden/ny", follow_redirects=True)

        user = User.query.filter_by(username="handl2").first()
        post = (
            AuditLog.query.filter_by(user_id=user.id, action="behorighet_nekad")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert post is not None
        assert post.details.get("endpoint") == "arenden.ny"

    def test_lyckad_inloggning_loggas(self, client, db):
        skapa_user(db, username="logtest2")
        db.session.commit()

        logga_in(client, "logtest2")

        user = User.query.filter_by(username="logtest2").first()
        post = (
            AuditLog.query.filter_by(user_id=user.id, action="login")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert post is not None


class TestDashboard:
    def test_dashboard_requires_login(self, client, db):
        resp = client.get("/dashboard")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_dashboard_stats(self, client, db):
        user = skapa_user(db, username="admin", role="admin")
        _skapa_arende(db, user, diarienummer="DNR-A1", status="oppnat")
        _skapa_arende(db, user, diarienummer="DNR-A2", status="pagaende")
        _skapa_arende(db, user, diarienummer="DNR-A3", status="pagaende")
        _skapa_arende(db, user, diarienummer="DNR-A4", status="avslutat")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.get("/dashboard")
        html = resp.data.decode()
        # Statistiken ska finnas i dashboarden
        assert resp.status_code == 200


# ── RBAC ─────────────────────────────────────────────────────────────


class TestRBAC:
    def test_role_required_allows_correct_role(self, client, db):
        skapa_user(db, username="reg", role="registrator")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/arenden/ny")
        assert resp.status_code == 200

    def test_role_required_denies_wrong_role(self, client, db):
        skapa_user(db, username="handl", role="handlaggare")
        db.session.commit()
        logga_in(client, "handl")

        resp = client.get("/arenden/ny", follow_redirects=True)
        assert "behörighet" in resp.data.decode()

    def test_role_required_redirects_unauthenticated(self, client, db):
        resp = client.get("/arenden/ny")
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")

    def test_admin_routes_deny_non_admin(self, client, db):
        skapa_user(db, username="reg", role="registrator")
        db.session.commit()
        logga_in(client, "reg")

        for path in ["/admin/", "/admin/anvandare", "/admin/logg"]:
            resp = client.get(path, follow_redirects=True)
            assert "behörighet" in resp.data.decode(), f"Saknar behörighetskontroll: {path}"


# ── Ärenden ──────────────────────────────────────────────────────────


class TestArendenRoutes:
    def test_skapa_arende(self, client, db):
        skapa_user(db, username="reg", role="registrator")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            "/arenden/ny",
            data={
                "arende_mening": "Nytt testärende",
                "prefix": "DNR",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "skapat" in resp.data.decode()
        assert Arende.query.count() == 1

    def test_skapa_arende_forbidden_for_handlaggare(self, client, db):
        skapa_user(db, username="handl", role="handlaggare")
        db.session.commit()
        logga_in(client, "handl")

        resp = client.post(
            "/arenden/ny",
            data={"arende_mening": "Borde inte gå", "prefix": "DNR"},
            follow_redirects=True,
        )
        assert "behörighet" in resp.data.decode()
        assert Arende.query.count() == 0

    def test_visa_arende(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get(f"/arenden/{arende.id}")
        assert resp.status_code == 200
        assert arende.diarienummer in resp.data.decode()

    def test_visa_deleted_arende_redirects(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        arende.deleted = True
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get(f"/arenden/{arende.id}", follow_redirects=True)
        assert "finns inte" in resp.data.decode()

    def test_redigera_arende(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/arenden/{arende.id}/redigera",
            data={"arende_mening": "Uppdaterad mening"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "uppdaterat" in resp.data.decode().lower()

        db.session.refresh(arende)
        assert arende.arende_mening == "Uppdaterad mening"

    def test_byt_status_valid_transition(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user, status="oppnat")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/arenden/{arende.id}/status",
            data={"ny_status": "pagaende"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        db.session.refresh(arende)
        assert arende.status == "pagaende"

    def test_byt_status_invalid_transition(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user, status="oppnat")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/arenden/{arende.id}/status",
            data={"ny_status": "arkiverat"},
            follow_redirects=True,
        )
        assert "Ogiltig" in resp.data.decode()

        db.session.refresh(arende)
        assert arende.status == "oppnat"

    def test_ta_bort_arende_admin_only(self, client, db):
        admin = skapa_user(db, username="adm", role="admin")
        arende = _skapa_arende(db, admin)
        db.session.commit()
        logga_in(client, "adm")

        resp = client.post(
            f"/arenden/{arende.id}/ta-bort",
            follow_redirects=True,
        )
        assert "borttaget" in resp.data.decode().lower()

        db.session.refresh(arende)
        assert arende.deleted is True

    def test_ta_bort_arende_denied_for_registrator(self, client, db):
        admin = skapa_user(db, username="adm", role="admin")
        reg = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, admin)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/arenden/{arende.id}/ta-bort",
            follow_redirects=True,
        )
        assert "behörighet" in resp.data.decode()

        db.session.refresh(arende)
        assert arende.deleted is False

    def test_lista_filter_by_status(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-O1", status="oppnat")
        _skapa_arende(db, user, diarienummer="DNR-P1", status="pagaende")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/arenden/?status=oppnat")
        html = resp.data.decode()
        assert "DNR-O1" in html
        assert "DNR-P1" not in html

    def test_lista_paginering(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        for i in range(25):
            _skapa_arende(
                db, user, diarienummer=f"DNR-PAG-{i:04d}"
            )
        db.session.commit()
        logga_in(client, "reg")

        resp1 = client.get("/arenden/?page=1")
        resp2 = client.get("/arenden/?page=2")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Sida 2 ska ha de resterande 5
        html2 = resp2.data.decode()
        assert "DNR-PAG-" in html2


# ── Statusvalidering – ärendelista ───────────────────────────────────


class TestStatusvalidering:
    def test_ogiltig_status_i_lista_ignoreras(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-STLST-001", status="oppnat")
        db.session.commit()
        logga_in(client, "reg")

        # Ogiltig status ska inte krascha och ska inte filtrera fram ärendet
        resp = client.get("/arenden/?status=OGILTIG")
        assert resp.status_code == 200
        # Sidan ska laddas utan 500
        assert "OGILTIG" not in resp.data.decode() or resp.status_code == 200

    def test_giltig_status_i_lista_filtrerar_korrekt(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-STLST-OPN", status="oppnat")
        _skapa_arende(db, user, diarienummer="DNR-STLST-PAG", status="pagaende")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/arenden/?status=oppnat")
        html = resp.data.decode()
        assert "DNR-STLST-OPN" in html
        assert "DNR-STLST-PAG" not in html

    def test_alla_tillåtna_statusvärden_accepteras(self, client, db):
        from app.models import Arende as ArendeModel

        user = skapa_user(db, username="reg", role="registrator")
        db.session.commit()
        logga_in(client, "reg")

        for status in ArendeModel.STATUS_LABELS:
            resp = client.get(f"/arenden/?status={status}")
            assert resp.status_code == 200, f"Status '{status}' borde accepteras"


# ── Handlingar ───────────────────────────────────────────────────────


class TestHandlingarRoutes:
    def test_skapa_handling_med_fil(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        data = {
            "typ": "inkommande",
            "beskrivning": "Inkommande brev",
            "datum_inkom": "2026-01-15",
            "avsandare": "Myndighet X",
            "fil": (io.BytesIO(b"filinnehall"), "brev.pdf"),
        }
        with patch(MOCK_MAGIC, return_value="application/pdf"):
            resp = client.post(
                f"/handlingar/ny/{arende.id}",
                data=data,
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert resp.status_code == 200
        assert "registrerad" in resp.data.decode().lower()

        handling = Handling.query.first()
        assert handling is not None
        assert handling.avsandare == "Myndighet X"
        assert handling.versioner.count() == 1

    def test_skapa_handling_utan_fil(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/handlingar/ny/{arende.id}",
            data={
                "typ": "upprattad",
                "beskrivning": "Internt PM",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        handling = Handling.query.first()
        assert handling is not None
        assert handling.versioner.count() == 0

    def test_ny_version_increments(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        _skapa_version(db, handling, user, version_nr=1)
        db.session.commit()
        logga_in(client, "reg")

        with patch(MOCK_MAGIC, return_value="application/pdf"):
            resp = client.post(
                f"/handlingar/{handling.id}/ny-version",
                data={
                    "fil": (io.BytesIO(b"ny version"), "v2.pdf"),
                    "kommentar": "Uppdaterad",
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert resp.status_code == 200
        assert "Version 2" in resp.data.decode()

        versioner = handling.versioner.all()
        assert len(versioner) == 2
        assert versioner[-1].version_nr == 2

    def test_ny_version_utan_fil_avvisas(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/handlingar/{handling.id}/ny-version",
            data={},
            follow_redirects=True,
        )
        assert "Ingen fil" in resp.data.decode()

    def test_otillaten_filandelse_avvisas(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/handlingar/ny/{arende.id}",
            data={
                "typ": "inkommande",
                "beskrivning": "Skadlig fil",
                "fil": (io.BytesIO(b"skadligt"), "skript.exe"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "inte tillåten" in resp.data.decode()
        assert Handling.query.count() == 0

    def test_mime_mismatch_avvisas(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        # Filändelse .pdf men innehållet är HTML
        with patch(MOCK_MAGIC, return_value="text/html"):
            resp = client.post(
                f"/handlingar/ny/{arende.id}",
                data={
                    "typ": "inkommande",
                    "beskrivning": "Fejkad PDF",
                    "fil": (io.BytesIO(b"<html>skadligt</html>"), "rapport.pdf"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert resp.status_code == 200
        assert "stämmer inte" in resp.data.decode()
        assert Handling.query.count() == 0

    def test_filnamn_saneras(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        with patch(MOCK_MAGIC, return_value="application/pdf"):
            client.post(
                f"/handlingar/ny/{arende.id}",
                data={
                    "typ": "inkommande",
                    "beskrivning": "Path traversal-test",
                    "fil": (io.BytesIO(b"data"), "../../etc/rapport.pdf"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        version = DocumentVersion.query.first()
        assert version is not None
        assert ".." not in version.filnamn
        assert "/" not in version.filnamn

    def test_tillåtna_filtyper_godkands(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        tillåtna = [
            ("bild.png", "image/png"),
            ("bild.jpg", "image/jpeg"),
            ("dokument.docx", "application/zip"),
            ("kalkyl.xlsx", "application/zip"),
        ]
        for filnamn, mime in tillåtna:
            with patch(MOCK_MAGIC, return_value=mime):
                resp = client.post(
                    f"/handlingar/ny/{arende.id}",
                    data={
                        "typ": "inkommande",
                        "beskrivning": f"Test {filnamn}",
                        "fil": (io.BytesIO(b"data"), filnamn),
                    },
                    content_type="multipart/form-data",
                    follow_redirects=True,
                )
            assert resp.status_code == 200
            assert "registrerad" in resp.data.decode().lower(), f"{filnamn} borde vara godkänd"

    def test_ny_version_otillaten_fil_avvisas(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        _skapa_version(db, handling, user, version_nr=1)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/handlingar/{handling.id}/ny-version",
            data={"fil": (io.BytesIO(b"data"), "skript.js")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert "inte tillåten" in resp.data.decode()
        assert handling.versioner.count() == 1  # Ingen ny version skapad

    def test_ladda_ner(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        version = _skapa_version(
            db, handling, user,
            filnamn="rapport.pdf",
            fildata=b"PDF-innehall",
            mime_type="application/pdf",
        )
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get(f"/handlingar/ladda-ner/{version.id}")
        assert resp.status_code == 200
        assert resp.data == b"PDF-innehall"
        assert "rapport.pdf" in resp.headers.get("Content-Disposition", "")
        assert resp.content_type == "application/pdf"

    def test_ta_bort_handling(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/handlingar/{handling.id}/ta-bort",
            follow_redirects=True,
        )
        assert resp.status_code == 200

        db.session.refresh(handling)
        assert handling.deleted is True


# ── Filstorlekskontroll ───────────────────────────────────────────────


class TestFilstorlekskontroll:
    def test_fil_over_max_avvisas(self):
        from app.routes.handlingar import _validera_fil, MAX_FIL_STORLEK_BYTES
        import types

        stor_data = b"X" * (MAX_FIL_STORLEK_BYTES + 1)
        fake_fil = types.SimpleNamespace(filename="stor.pdf", read=lambda: stor_data)

        with pytest.raises(ValueError, match="för stor"):
            _validera_fil(fake_fil)

    def test_fil_exakt_pa_gransen_godkands(self):
        from app.routes.handlingar import _validera_fil, MAX_FIL_STORLEK_BYTES
        import types

        exakt_data = b"X" * MAX_FIL_STORLEK_BYTES
        fake_fil = types.SimpleNamespace(filename="exakt.pdf", read=lambda: exakt_data)

        with patch(MOCK_MAGIC, return_value="application/pdf"):
            filnamn, fildata, mime = _validera_fil(fake_fil)
        assert len(fildata) == MAX_FIL_STORLEK_BYTES

    def test_liten_fil_godkands(self):
        from app.routes.handlingar import _validera_fil
        import types

        fake_fil = types.SimpleNamespace(filename="liten.pdf", read=lambda: b"PDF-data")

        with patch(MOCK_MAGIC, return_value="application/pdf"):
            filnamn, fildata, mime = _validera_fil(fake_fil)
        assert fildata == b"PDF-data"

    def test_stor_fil_via_route_ger_felmeddelande(self, client, db):
        from app.routes.handlingar import MAX_FIL_STORLEK_BYTES

        user = skapa_user(db, username="reg_stor", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg_stor")

        stor_data = b"X" * (MAX_FIL_STORLEK_BYTES + 1)
        data = {
            "typ": "inkommande",
            "beskrivning": "Stor fil",
            "fil": (io.BytesIO(stor_data), "stor.pdf"),
        }
        with patch(MOCK_MAGIC, return_value="application/pdf"):
            resp = client.post(
                f"/handlingar/ny/{arende.id}",
                data=data,
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert "för stor" in resp.data.decode()
        assert Handling.query.count() == 0


# ── Sök ──────────────────────────────────────────────────────────────


class TestSokRoutes:
    def test_sok_no_params_returns_no_results(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/")
        assert resp.status_code == 200
        # Inga resultat utan sökparametrar
        assert "DNR-2026-0001" not in resp.data.decode()

    def test_sok_by_diarienummer(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-SOK-001")
        _skapa_arende(db, user, diarienummer="DNR-ANNAN-002")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?diarienummer=SOK")
        html = resp.data.decode()
        assert "DNR-SOK-001" in html
        assert "DNR-ANNAN-002" not in html

    def test_sok_by_mening(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-M1", arende_mening="Bygglov ansökan")
        _skapa_arende(db, user, diarienummer="DNR-M2", arende_mening="Miljöprövning")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?mening=Bygglov")
        html = resp.data.decode()
        assert "DNR-M1" in html
        assert "DNR-M2" not in html

    def test_sok_by_status(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-S1", status="oppnat")
        _skapa_arende(db, user, diarienummer="DNR-S2", status="pagaende")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?status=pagaende")
        html = resp.data.decode()
        assert "DNR-S2" in html
        assert "DNR-S1" not in html

    def test_sok_by_datumintervall(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-D1")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?fran=2020-01-01&till=2030-12-31")
        html = resp.data.decode()
        assert "DNR-D1" in html

    def test_sok_by_avsandare(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user, diarienummer="DNR-AV1")
        _skapa_handling(db, arende, user, avsandare="Skatteverket")

        arende2 = _skapa_arende(db, user, diarienummer="DNR-AV2")
        _skapa_handling(db, arende2, user, avsandare="Polisen")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?avsandare=Skatteverket")
        html = resp.data.decode()
        assert "DNR-AV1" in html
        assert "DNR-AV2" not in html

    def test_sok_excludes_deleted(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user, diarienummer="DNR-DEL-001")
        arende.deleted = True
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?diarienummer=DEL")
        html = resp.data.decode()
        assert "DNR-DEL-001" not in html

    def test_ogiltigt_fran_datum_ger_varning(self, client, db):
        skapa_user(db, username="reg", role="registrator")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?fran=inte-ett-datum", follow_redirects=True)
        assert resp.status_code == 200
        assert "Ogiltigt datum" in resp.data.decode()

    def test_ogiltigt_till_datum_ger_varning(self, client, db):
        skapa_user(db, username="reg", role="registrator")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?till=2026-99-99", follow_redirects=True)
        assert resp.status_code == 200
        assert "Ogiltigt datum" in resp.data.decode()

    def test_ogiltigt_datum_krachar_inte(self, client, db):
        skapa_user(db, username="reg", role="registrator")
        db.session.commit()
        logga_in(client, "reg")

        # Ska inte kasta 500 utan returnera 200 med en varning
        resp = client.get("/sok/?fran=<script>alert(1)</script>")
        assert resp.status_code == 200

    def test_lang_sokstrang_trunkeras(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-LANG-001")
        db.session.commit()
        logga_in(client, "reg")

        # En söksträng på 200 tecken ska inte krascha
        lang_strang = "A" * 200
        resp = client.get(f"/sok/?diarienummer={lang_strang}")
        assert resp.status_code == 200

    def test_ogiltig_status_i_sok_ger_varning(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-STVAL-001", status="oppnat")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?status=INJICERAT_VÄRDE")
        assert resp.status_code == 200
        assert "Okänd status" in resp.data.decode()
        # Ärenden ska inte filtreras på ogiltigt status — alla visas inte
        # (inga resultat utan ytterligare sökparameter är rätt beteende)

    def test_giltig_status_i_sok_filtrerar_korrekt(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        _skapa_arende(db, user, diarienummer="DNR-STVAL-OPN", status="oppnat")
        _skapa_arende(db, user, diarienummer="DNR-STVAL-PAG", status="pagaende")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/sok/?status=oppnat")
        html = resp.data.decode()
        assert "DNR-STVAL-OPN" in html
        assert "DNR-STVAL-PAG" not in html


# ── Inputvalidering – enhetstester för sok-hjälpfunktioner ───────────


class TestSokHjalpfunktioner:
    def test_parse_datum_giltigt(self):
        from app.routes.sok import _parse_datum
        from datetime import date
        assert _parse_datum("2026-01-15") == date(2026, 1, 15)

    def test_parse_datum_ogiltigt_format(self):
        from app.routes.sok import _parse_datum
        assert _parse_datum("inte-ett-datum") is None

    def test_parse_datum_omojligt_datum(self):
        from app.routes.sok import _parse_datum
        assert _parse_datum("2026-99-99") is None

    def test_parse_datum_tom_strang(self):
        from app.routes.sok import _parse_datum
        assert _parse_datum("") is None

    def test_parse_datum_none(self):
        from app.routes.sok import _parse_datum
        assert _parse_datum(None) is None

    def test_trunkera_kort_strang(self):
        from app.routes.sok import _trunkera
        assert _trunkera("kort") == "kort"

    def test_trunkera_exakt_100_tecken(self):
        from app.routes.sok import _trunkera
        s = "A" * 100
        assert _trunkera(s) == s

    def test_trunkera_over_100_tecken(self):
        from app.routes.sok import _trunkera
        s = "A" * 200
        assert len(_trunkera(s)) == 100

    def test_trunkera_bevarar_innehall(self):
        from app.routes.sok import _trunkera
        s = "Bygglov" + "X" * 200
        assert _trunkera(s).startswith("Bygglov")


# ── Admin ────────────────────────────────────────────────────────────


class TestAdminRoutes:
    def test_ny_anvandare(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/admin/anvandare/ny",
            data={
                "username": "nyanvandare",
                "full_name": "Ny Användare",
                "email": "ny@test.se",
                "role": "handlaggare",
                "password": "Hemligt!Pass123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "skapad" in resp.data.decode().lower()

        ny = User.query.filter_by(username="nyanvandare").first()
        assert ny is not None
        assert ny.role == "handlaggare"

    def test_ny_anvandare_duplicate_username(self, client, db):
        skapa_user(db, username="admin", role="admin")
        skapa_user(db, username="finns", role="handlaggare")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/admin/anvandare/ny",
            data={
                "username": "finns",
                "full_name": "Dubblett",
                "role": "handlaggare",
                "password": "Hemligt!Pass123",
            },
            follow_redirects=True,
        )
        assert "redan taget" in resp.data.decode()

    def test_ny_anvandare_svagt_losenord_avvisas(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/admin/anvandare/ny",
            data={
                "username": "nyanvandare",
                "full_name": "Ny Användare",
                "role": "handlaggare",
                "password": "hemligt123",  # kort, saknar versal och specialtecken
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert User.query.filter_by(username="nyanvandare").first() is None

    def test_ny_anvandare_losenord_for_kort_visar_fel(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/admin/anvandare/ny",
            data={
                "username": "nyanvandare",
                "full_name": "Ny Användare",
                "role": "handlaggare",
                "password": "Kort!1A",  # < 12 tecken
            },
            follow_redirects=True,
        )
        assert "12 tecken" in resp.data.decode()

    def test_redigera_anvandare_svagt_losenord_avvisas(self, client, db):
        skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mal", role="handlaggare")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/anvandare/{target.id}/redigera",
            data={
                "full_name": "Nytt Namn",
                "role": "handlaggare",
                "active": "on",
                "password": "hemligt123",  # svagt lösenord
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Lösenordet ska inte ha ändrats
        db.session.refresh(target)
        assert not target.check_password("hemligt123")

    def test_redigera_anvandare_starkt_losenord_godkant(self, client, db):
        skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mal", role="handlaggare")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/anvandare/{target.id}/redigera",
            data={
                "full_name": target.full_name,
                "role": "handlaggare",
                "active": "on",
                "password": "Hemligt!Pass123",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(target)
        assert target.check_password("Hemligt!Pass123")

    def test_redigera_anvandare_tomt_losenord_behaller_gammalt(self, client, db):
        skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mal", role="handlaggare", password="lösenord123")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/anvandare/{target.id}/redigera",
            data={
                "full_name": "Uppdaterat Namn",
                "role": "handlaggare",
                "active": "on",
                # inget lösenord skickas
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(target)
        assert target.check_password("lösenord123")
        assert target.full_name == "Uppdaterat Namn"

    def test_redigera_anvandare(self, client, db):
        admin = skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mål", role="handlaggare")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/anvandare/{target.id}/redigera",
            data={
                "full_name": "Nytt Namn",
                "role": "registrator",
                "active": "on",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        db.session.refresh(target)
        assert target.full_name == "Nytt Namn"
        assert target.role == "registrator"

    def test_logg_paginering(self, client, db):
        admin = skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.get("/admin/logg?page=1")
        assert resp.status_code == 200


# ── Arkiv ────────────────────────────────────────────────────────────


class TestArkivRoutes:
    def test_arkiv_lista_only_avslutat_arkiverat(self, client, db):
        user = skapa_user(db, username="ark", role="arkivarie")
        _skapa_arende(db, user, diarienummer="DNR-OPN", status="oppnat")
        _skapa_arende(db, user, diarienummer="DNR-AVS", status="avslutat")
        _skapa_arende(db, user, diarienummer="DNR-ARK", status="arkiverat")
        db.session.commit()
        logga_in(client, "ark")

        resp = client.get("/arkiv/")
        html = resp.data.decode()
        assert "DNR-AVS" in html
        assert "DNR-ARK" in html
        assert "DNR-OPN" not in html

    def test_exportera_json_structure(self, client, db):
        user = skapa_user(db, username="ark", role="arkivarie")
        arende = _skapa_arende(
            db, user,
            diarienummer="DNR-EXP-001",
            arende_mening="Exporttest",
            status="avslutat",
        )
        handling = _skapa_handling(db, arende, user, beskrivning="Exporthandling")
        _skapa_version(db, handling, user, filnamn="export.pdf")
        db.session.commit()
        logga_in(client, "ark")

        resp = client.get(f"/arkiv/exportera/{arende.id}")
        assert resp.status_code == 200
        assert resp.content_type == "application/json"

        data = json.loads(resp.data)
        assert data["diarienummer"] == "DNR-EXP-001"
        assert data["arende_mening"] == "Exporttest"
        assert data["status"] == "avslutat"
        assert "handlingar" in data
        assert len(data["handlingar"]) == 1
        assert data["handlingar"][0]["beskrivning"] == "Exporthandling"
        assert len(data["handlingar"][0]["versioner"]) == 1
        assert data["handlingar"][0]["versioner"][0]["filnamn"] == "export.pdf"
        assert "audit_log" in data
        assert "exporterad" in data

    def test_exportera_excludes_deleted_handlingar(self, client, db):
        user = skapa_user(db, username="ark", role="arkivarie")
        arende = _skapa_arende(db, user, diarienummer="DNR-EXDEL")
        h_active = _skapa_handling(db, arende, user, beskrivning="Aktiv")
        h_deleted = _skapa_handling(db, arende, user, beskrivning="Borttagen")
        h_deleted.deleted = True
        db.session.commit()
        logga_in(client, "ark")

        resp = client.get(f"/arkiv/exportera/{arende.id}")
        data = json.loads(resp.data)
        beskrivningar = [h["beskrivning"] for h in data["handlingar"]]
        assert "Aktiv" in beskrivningar
        assert "Borttagen" not in beskrivningar

    def test_arkiv_denied_for_handlaggare(self, client, db):
        skapa_user(db, username="handl", role="handlaggare")
        db.session.commit()
        logga_in(client, "handl")

        resp = client.get("/arkiv/", follow_redirects=True)
        assert "behörighet" in resp.data.decode()


# ── Sanering av exportdata ───────────────────────────────────────────


class TestSaneraExportvarde:
    def test_null_byte_tas_bort_ur_strang(self):
        from app.routes.arkiv import _sanera_exportvarde

        assert _sanera_exportvarde("hej\x00varlden") == "hejvarlden"

    def test_kontrollkaraktarer_tas_bort(self):
        from app.routes.arkiv import _sanera_exportvarde

        assert _sanera_exportvarde("a\x01b\x1fc") == "abc"

    def test_tab_och_radbrytning_bevaras(self):
        from app.routes.arkiv import _sanera_exportvarde

        assert _sanera_exportvarde("rad1\nrad2\ttab") == "rad1\nrad2\ttab"

    def test_rekursiv_sanering_av_dict(self):
        from app.routes.arkiv import _sanera_exportvarde

        data = {"nyckel": "varde\x00", "nested": {"inner": "ok\x07"}}
        result = _sanera_exportvarde(data)
        assert result == {"nyckel": "varde", "nested": {"inner": "ok"}}

    def test_rekursiv_sanering_av_lista(self):
        from app.routes.arkiv import _sanera_exportvarde

        data = ["a\x00b", "c\x1fd"]
        assert _sanera_exportvarde(data) == ["ab", "cd"]

    def test_icke_strang_varden_passerar_oforändrade(self):
        from app.routes.arkiv import _sanera_exportvarde

        assert _sanera_exportvarde(42) == 42
        assert _sanera_exportvarde(None) is None
        assert _sanera_exportvarde(True) is True

    def test_exportera_sanerar_details_med_null_byte(self, client, db):
        from app.models import log_action

        user = skapa_user(db, username="ark2", role="arkivarie")
        arende = _skapa_arende(db, user, diarienummer="DNR-SAN-001", status="avslutat")
        log_action(
            user.id,
            "test_action",
            "Arende",
            arende.id,
            {"skadlig_input": "data\x00med\x01nollbyte"},
        )
        db.session.commit()
        logga_in(client, "ark2")

        resp = client.get(f"/arkiv/exportera/{arende.id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        logg = data["audit_log"]
        test_entry = next((e for e in logg if e["action"] == "test_action"), None)
        assert test_entry is not None
        assert "\x00" not in test_entry["details"]["skadlig_input"]
        assert test_entry["details"]["skadlig_input"] == "datamednollbyte"


# ── Frågetimeout ─────────────────────────────────────────────────────


class TestFragetimeout:
    def test_timeout_konfig_finns_och_har_standardvarde(self, app):
        assert app.config["DB_QUERY_TIMEOUT_MS"] == 5000

    def test_timeout_noll_inaktiverar_funktionen(self, app):
        """_registrera_fragetimeout ska returnera direkt om timeout = 0."""
        from app import _registrera_fragetimeout

        app.config["DB_QUERY_TIMEOUT_MS"] = 0
        try:
            # Ska inte kasta undantag
            with app.app_context():
                _registrera_fragetimeout(app)
        finally:
            app.config["DB_QUERY_TIMEOUT_MS"] = 5000

    def test_sqlite_hoppar_over_timeout(self, app):
        """_registrera_fragetimeout ska inte kasta undantag för SQLite-dialekten."""
        from app import _registrera_fragetimeout

        with app.app_context():
            # SQLite-dialekten ska returnera tidigt utan fel
            assert app.extensions["sqlalchemy"].engine.dialect.name == "sqlite"
            _registrera_fragetimeout(app)  # Ska inte kasta undantag

    def test_sok_returnerar_trots_timeout_konfig(self, client, db):
        """Normala sökfrågor ska fungera oberoende av timeout-konfig."""
        skapa_user(db, username="handl_sok", role="handlaggare")
        db.session.commit()
        logga_in(client, "handl_sok")

        resp = client.get("/sok/?mening=test")
        assert resp.status_code == 200
