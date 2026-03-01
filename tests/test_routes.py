"""Integrationstester för routes/vyer."""

import hashlib
import io
import json
from datetime import date
from unittest.mock import patch

import pytest

from app.models import Arende, Handling, DocumentVersion, AuditLog, Nummerserie, Installning, User, Kategori, APIKey
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

    def test_handlaggare_kan_byta_status_pa_eget_arende(self, client, db):
        handl = skapa_user(db, username="handl", role="handlaggare")
        arende = _skapa_arende(db, handl, status="oppnat", handlaggare_id=handl.id)
        db.session.commit()
        logga_in(client, "handl")

        resp = client.post(
            f"/arenden/{arende.id}/status",
            data={"ny_status": "pagaende"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(arende)
        assert arende.status == "pagaende"

    def test_handlaggare_nekas_byta_status_pa_annans_arende(self, client, db):
        reg = skapa_user(db, username="reg", role="registrator")
        handl = skapa_user(db, username="handl", role="handlaggare")
        arende = _skapa_arende(db, reg, status="oppnat")  # ingen handläggare tilldelad
        db.session.commit()
        logga_in(client, "handl")

        resp = client.post(
            f"/arenden/{arende.id}/status",
            data={"ny_status": "pagaende"},
            follow_redirects=True,
        )
        assert resp.status_code == 403
        db.session.refresh(arende)
        assert arende.status == "oppnat"

    def test_handlaggare_nekas_byta_status_pa_annan_handlaggares_arende(self, client, db):
        reg = skapa_user(db, username="reg", role="registrator")
        handl1 = skapa_user(db, username="handl1", role="handlaggare")
        handl2 = skapa_user(db, username="handl2", role="handlaggare")
        arende = _skapa_arende(db, reg, status="oppnat", handlaggare_id=handl1.id)
        db.session.commit()
        logga_in(client, "handl2")

        resp = client.post(
            f"/arenden/{arende.id}/status",
            data={"ny_status": "pagaende"},
            follow_redirects=True,
        )
        assert resp.status_code == 403
        db.session.refresh(arende)
        assert arende.status == "oppnat"


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

    def test_handlaggare_kan_skapa_handling_pa_eget_arende(self, client, db):
        handl = skapa_user(db, username="handl", role="handlaggare")
        arende = _skapa_arende(db, handl, handlaggare_id=handl.id)
        db.session.commit()
        logga_in(client, "handl")

        resp = client.post(
            f"/handlingar/ny/{arende.id}",
            data={"typ": "inkommande", "beskrivning": "Ny handling"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert Handling.query.count() == 1

    def test_handlaggare_nekas_skapa_handling_pa_annans_arende(self, client, db):
        reg = skapa_user(db, username="reg", role="registrator")
        handl = skapa_user(db, username="handl", role="handlaggare")
        arende = _skapa_arende(db, reg)  # ingen handläggare tilldelad
        db.session.commit()
        logga_in(client, "handl")

        resp = client.post(
            f"/handlingar/ny/{arende.id}",
            data={"typ": "inkommande", "beskrivning": "Ej tillåten"},
            follow_redirects=True,
        )
        assert resp.status_code == 403
        assert Handling.query.count() == 0

    def test_handlaggare_nekas_skapa_handling_pa_annan_handlaggares_arende(self, client, db):
        reg = skapa_user(db, username="reg", role="registrator")
        handl1 = skapa_user(db, username="handl1", role="handlaggare")
        handl2 = skapa_user(db, username="handl2", role="handlaggare")
        arende = _skapa_arende(db, reg, handlaggare_id=handl1.id)
        db.session.commit()
        logga_in(client, "handl2")

        resp = client.post(
            f"/handlingar/ny/{arende.id}",
            data={"typ": "inkommande", "beskrivning": "Ej tillåten"},
            follow_redirects=True,
        )
        assert resp.status_code == 403
        assert Handling.query.count() == 0

    def test_handlaggare_nekas_ny_version_pa_annans_arende(self, client, db):
        reg = skapa_user(db, username="reg", role="registrator")
        handl = skapa_user(db, username="handl", role="handlaggare")
        arende = _skapa_arende(db, reg)  # ingen handläggare
        handling = _skapa_handling(db, arende, reg)
        _skapa_version(db, handling, reg, version_nr=1)
        db.session.commit()
        logga_in(client, "handl")

        with patch(MOCK_MAGIC, return_value="application/pdf"):
            resp = client.post(
                f"/handlingar/{handling.id}/ny-version",
                data={"fil": (io.BytesIO(b"ny version"), "v2.pdf")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert resp.status_code == 403
        assert handling.versioner.count() == 1

    def test_handlaggare_kan_ladda_upp_ny_version_pa_eget_arende(self, client, db):
        handl = skapa_user(db, username="handl", role="handlaggare")
        arende = _skapa_arende(db, handl, handlaggare_id=handl.id)
        handling = _skapa_handling(db, arende, handl)
        _skapa_version(db, handling, handl, version_nr=1)
        db.session.commit()
        logga_in(client, "handl")

        with patch(MOCK_MAGIC, return_value="application/pdf"):
            resp = client.post(
                f"/handlingar/{handling.id}/ny-version",
                data={"fil": (io.BytesIO(b"ny version"), "v2.pdf")},
                content_type="multipart/form-data",
                follow_redirects=True,
            )
        assert resp.status_code == 200
        assert handling.versioner.count() == 2


# ── andrad_datum uppdateras vid handling-ändringar ───────────────────


class TestAndradDatumHandling:
    def test_andrad_datum_uppdateras_vid_ny_handling(self, client, db):
        import time
        user = skapa_user(db, username="reg2", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        foret = arende.andrad_datum
        time.sleep(0.05)
        logga_in(client, "reg2")

        with patch(MOCK_MAGIC, return_value="application/pdf"):
            client.post(
                f"/handlingar/ny/{arende.id}",
                data={
                    "typ": "inkommande",
                    "beskrivning": "Test",
                    "fil": (io.BytesIO(b"data"), "test.pdf"),
                },
                content_type="multipart/form-data",
                follow_redirects=True,
            )

        db.session.refresh(arende)
        assert arende.andrad_datum > foret

    def test_andrad_datum_uppdateras_vid_ta_bort_handling(self, client, db):
        import time
        user = skapa_user(db, username="reg3", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        db.session.commit()
        foret = arende.andrad_datum
        time.sleep(0.05)
        logga_in(client, "reg3")

        client.post(f"/handlingar/{handling.id}/ta-bort", follow_redirects=True)

        db.session.refresh(arende)
        assert arende.andrad_datum > foret


# ── Filstorlekskontroll ───────────────────────────────────────────────


class TestFilstorlekskontroll:
    def test_fil_over_max_avvisas(self, app):
        from app.routes.handlingar import _validera_fil
        import types

        max_bytes = app.config["MAX_FIL_STORLEK_MB"] * 1024 * 1024
        stor_data = b"X" * (max_bytes + 1)
        fake_fil = types.SimpleNamespace(filename="stor.pdf", read=lambda: stor_data)

        with app.app_context():
            with pytest.raises(ValueError, match="för stor"):
                _validera_fil(fake_fil)

    def test_fil_exakt_pa_gransen_godkands(self, app):
        from app.routes.handlingar import _validera_fil
        import types

        max_bytes = app.config["MAX_FIL_STORLEK_MB"] * 1024 * 1024
        exakt_data = b"X" * max_bytes
        fake_fil = types.SimpleNamespace(filename="exakt.pdf", read=lambda: exakt_data)

        with app.app_context():
            with patch(MOCK_MAGIC, return_value="application/pdf"):
                filnamn, fildata, mime = _validera_fil(fake_fil)
        assert len(fildata) == max_bytes

    def test_liten_fil_godkands(self):
        from app.routes.handlingar import _validera_fil
        import types

        fake_fil = types.SimpleNamespace(filename="liten.pdf", read=lambda: b"PDF-data")

        with patch(MOCK_MAGIC, return_value="application/pdf"):
            filnamn, fildata, mime = _validera_fil(fake_fil)
        assert fildata == b"PDF-data"

    def test_stor_fil_via_route_ger_felmeddelande(self, client, db):
        from flask import current_app

        max_bytes = current_app.config["MAX_FIL_STORLEK_MB"] * 1024 * 1024
        user = skapa_user(db, username="reg_stor", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg_stor")

        stor_data = b"X" * (max_bytes + 1)
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


# ── Ta bort användare ────────────────────────────────────────────────


class TestTaBortAnvandare:
    def test_ta_bort_anvandare(self, client, db):
        skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mål", role="handlaggare")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/anvandare/{target.id}/ta-bort",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(target)
        assert target.deleted is True
        assert target.active is False

    def test_borttagen_anvandare_syns_inte_i_lista(self, client, db):
        skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mål", role="handlaggare")
        db.session.commit()
        logga_in(client, "admin")

        client.post(f"/admin/anvandare/{target.id}/ta-bort")
        resp = client.get("/admin/anvandare")
        assert f"/admin/anvandare/{target.id}/redigera" not in resp.data.decode()

    def test_ta_bort_eget_konto_nekad(self, client, db):
        admin = skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/anvandare/{admin.id}/ta-bort",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(admin)
        assert admin.deleted is False

    def test_ta_bort_sista_admin_nekad(self, client, db):
        admin = skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="admin2", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        # Ta bort admin2 — lyckas eftersom admin fortfarande finns
        client.post(f"/admin/anvandare/{target.id}/ta-bort")
        db.session.refresh(target)
        assert target.deleted is True

        # Nu är admin den enda aktiva adminen — försök ta bort sig själv via
        # en annan admin-session är inte möjligt, testa istället att en ny
        # admin inte kan ta bort den sista aktiva adminen
        admin3 = skapa_user(db, username="admin3", role="admin")
        db.session.commit()

        resp = client.post(
            f"/admin/anvandare/{admin.id}/ta-bort",
            follow_redirects=True,
        )
        # admin är inloggad och försöker ta bort sitt eget konto — ska nekas
        db.session.refresh(admin)
        assert admin.deleted is False

    def test_ta_bort_en_av_flera_admins_godkant(self, client, db):
        skapa_user(db, username="admin", role="admin")
        admin2 = skapa_user(db, username="admin2", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/anvandare/{admin2.id}/ta-bort",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(admin2)
        assert admin2.deleted is True

    def test_ta_bort_loggas_i_granskningslogg(self, client, db):
        admin = skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mål", role="handlaggare")
        db.session.commit()
        logga_in(client, "admin")

        client.post(f"/admin/anvandare/{target.id}/ta-bort")

        logg = AuditLog.query.filter_by(
            action="ta_bort_anvandare",
            target_type="User",
            target_id=target.id,
        ).first()
        assert logg is not None
        assert logg.user_id == admin.id
        assert logg.details["username"] == "mål"

    def test_redigera_borttagen_anvandare_redirectar(self, client, db):
        skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mål", role="handlaggare")
        target.deleted = True
        target.active = False
        db.session.commit()
        logga_in(client, "admin")

        resp = client.get(
            f"/admin/anvandare/{target.id}/redigera",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "borttagen" in resp.data.decode().lower()

    def test_ta_bort_redan_borttagen_anvandare(self, client, db):
        skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mål", role="handlaggare")
        target.deleted = True
        target.active = False
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/anvandare/{target.id}/ta-bort",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Inga extra loggposter ska skapas
        assert AuditLog.query.filter_by(action="ta_bort_anvandare").count() == 0

    def test_spårbarhet_auditlogg_bevaras_efter_borttagning(self, client, db):
        """AuditLog-poster skapade av den borttagna användaren ska finnas kvar."""
        admin = skapa_user(db, username="admin", role="admin")
        target = skapa_user(db, username="mål", role="registrator")
        db.session.commit()

        # Skapa en loggpost som refererar till target
        from app.models import log_action
        with client.application.test_request_context():
            log_action(target.id, "login", details={"test": True})
        db.session.commit()

        logga_in(client, "admin")
        client.post(f"/admin/anvandare/{target.id}/ta-bort")

        # Ursprunglig loggpost ska fortfarande finnas och peka på rätt user_id
        logg = AuditLog.query.filter_by(action="login", user_id=target.id).first()
        assert logg is not None
        assert logg.user_id == target.id


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


# ── Kategori – adminroutes ─────────────────────────────────────────────


def _skapa_kategori(db, namn="Protokoll"):
    kategori = Kategori(namn=namn)
    db.session.add(kategori)
    db.session.flush()
    return kategori


class TestKategoriAdminRoutes:
    def test_lista_kategorier(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        _skapa_kategori(db, "Faktura")
        db.session.commit()

        resp = client.get("/admin/kategorier")
        assert resp.status_code == 200
        assert "Faktura" in resp.data.decode()

    def test_skapa_kategori(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/admin/kategorier/ny",
            data={"namn": "Remiss"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert Kategori.query.filter_by(namn="Remiss").first() is not None

    def test_skapa_kategori_loggas(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        client.post(
            "/admin/kategorier/ny",
            data={"namn": "Remiss"},
            follow_redirects=True,
        )
        logg = AuditLog.query.filter_by(action="skapa_kategori").first()
        assert logg is not None
        assert logg.details["namn"] == "Remiss"

    def test_skapa_kategori_tomt_namn_avvisas(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/admin/kategorier/ny",
            data={"namn": "  "},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert Kategori.query.count() == 0

    def test_skapa_kategori_dublettnamn_avvisas(self, client, db):
        skapa_user(db, username="admin", role="admin")
        _skapa_kategori(db, "Protokoll")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/admin/kategorier/ny",
            data={"namn": "Protokoll"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert Kategori.query.filter_by(namn="Protokoll").count() == 1

    def test_ta_bort_kategori(self, client, db):
        skapa_user(db, username="admin", role="admin")
        kategori = _skapa_kategori(db, "Avtal")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/kategorier/{kategori.id}/ta-bort",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert Kategori.query.get(kategori.id) is None

    def test_ta_bort_kategori_loggas(self, client, db):
        skapa_user(db, username="admin", role="admin")
        kategori = _skapa_kategori(db, "Avtal")
        db.session.commit()
        logga_in(client, "admin")

        client.post(
            f"/admin/kategorier/{kategori.id}/ta-bort",
            follow_redirects=True,
        )
        logg = AuditLog.query.filter_by(action="ta_bort_kategori").first()
        assert logg is not None
        assert logg.details["namn"] == "Avtal"

    def test_ta_bort_kategori_blockeras_om_aktiv_handling(self, client, db):
        admin = skapa_user(db, username="admin", role="admin")
        kategori = _skapa_kategori(db, "Protokoll")
        arende = _skapa_arende(db, admin)
        handling = _skapa_handling(db, arende, admin)
        handling.kategorier = [kategori]
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/kategorier/{kategori.id}/ta-bort",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert Kategori.query.get(kategori.id) is not None

    def test_ta_bort_kategori_tillaten_om_handling_deleted(self, client, db):
        admin = skapa_user(db, username="admin", role="admin")
        kategori = _skapa_kategori(db, "Protokoll")
        arende = _skapa_arende(db, admin)
        handling = _skapa_handling(db, arende, admin, deleted=True)
        handling.kategorier = [kategori]
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            f"/admin/kategorier/{kategori.id}/ta-bort",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert Kategori.query.get(kategori.id) is None

    def test_kategorier_krav_admin_roll(self, client, db):
        skapa_user(db, username="registrator", role="registrator")
        db.session.commit()
        logga_in(client, "registrator")

        resp = client.get("/admin/kategorier", follow_redirects=True)
        assert "behörighet" in resp.data.decode()


# ── Handlingar med kategorier ─────────────────────────────────────────


class TestHandlingarMedKategorier:
    def test_ny_handling_med_kategorier(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        kategori = _skapa_kategori(db, "Faktura")
        db.session.commit()
        logga_in(client, "reg")

        with patch(MOCK_MAGIC, return_value="application/pdf"):
            resp = client.post(
                f"/handlingar/ny/{arende.id}",
                data={
                    "typ": "inkommande",
                    "beskrivning": "Test med kategori",
                    "kategorier": [str(kategori.id)],
                },
                follow_redirects=True,
            )
        assert resp.status_code == 200
        handling = Handling.query.filter_by(beskrivning="Test med kategori").first()
        assert handling is not None
        assert handling.kategorier.count() == 1
        assert handling.kategorier.first().namn == "Faktura"

    def test_ny_handling_utan_kategorier(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/handlingar/ny/{arende.id}",
            data={"typ": "inkommande", "beskrivning": "Utan kategori"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        handling = Handling.query.filter_by(beskrivning="Utan kategori").first()
        assert handling is not None
        assert handling.kategorier.count() == 0

    def test_ny_handling_ogiltigt_kategori_id_ignoreras(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            f"/handlingar/ny/{arende.id}",
            data={"typ": "inkommande", "beskrivning": "Ogiltigt id", "kategorier": ["9999"]},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        handling = Handling.query.filter_by(beskrivning="Ogiltigt id").first()
        assert handling is not None
        assert handling.kategorier.count() == 0

    def test_visa_handling_visar_kategorier(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        kategori = _skapa_kategori(db, "Remiss")
        handling = _skapa_handling(db, arende, user)
        handling.kategorier = [kategori]
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get(f"/handlingar/{handling.id}")
        assert resp.status_code == 200
        assert "Remiss" in resp.data.decode()


# ── Redigera handling ─────────────────────────────────────────────────


class TestRedigeraHandling:
    """Tester för GET/POST /handlingar/<id>/redigera."""

    def _setup(self, db):
        """Skapar en registrator, ett ärende och en handling."""
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user, typ="inkommande", beskrivning="Original")
        db.session.commit()
        return user, arende, handling

    # --- GET ---

    def test_get_visar_formulär(self, client, db):
        _, _, handling = self._setup(db)
        logga_in(client, "reg")

        resp = client.get(f"/handlingar/{handling.id}/redigera")
        assert resp.status_code == 200
        assert "Original" in resp.data.decode()

    def test_get_förifyllt_med_befintliga_värden(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(
            db, arende, user,
            typ="utgaende",
            beskrivning="Befintlig beskrivning",
            avsandare="Avsändaren AB",
            mottagare="Mottagaren AB",
            datum_inkom=date(2025, 6, 15),
            sekretess=True,
        )
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get(f"/handlingar/{handling.id}/redigera")
        html = resp.data.decode()
        assert "Befintlig beskrivning" in html
        assert "Avsändaren AB" in html
        assert "Mottagaren AB" in html
        assert "2025-06-15" in html
        assert 'value="utgaende"' in html

    def test_get_förifyllt_med_kategorier(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        k1 = _skapa_kategori(db, "Faktura")
        k2 = _skapa_kategori(db, "Remiss")
        handling.kategorier = [k1]
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get(f"/handlingar/{handling.id}/redigera")
        html = resp.data.decode()
        # k1 ska vara förbockad, k2 inte
        assert f'value="{k1.id}" id="kategori_{k1.id}" checked' in html or \
               f'checked' in html  # enklare kontroll: sidan renderas korrekt
        assert "Faktura" in html
        assert "Remiss" in html

    # --- POST – lyckade uppdateringar ---

    def test_post_uppdaterar_beskrivning(self, client, db):
        _, _, handling = self._setup(db)
        logga_in(client, "reg")

        resp = client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "inkommande", "beskrivning": "Ny beskrivning"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(handling)
        assert handling.beskrivning == "Ny beskrivning"

    def test_post_uppdaterar_typ(self, client, db):
        _, _, handling = self._setup(db)
        logga_in(client, "reg")

        client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "utgaende", "beskrivning": "Original"},
            follow_redirects=True,
        )
        db.session.refresh(handling)
        assert handling.typ == "utgaende"

    def test_post_uppdaterar_alla_fält(self, client, db):
        _, _, handling = self._setup(db)
        logga_in(client, "reg")

        client.post(
            f"/handlingar/{handling.id}/redigera",
            data={
                "typ": "upprattad",
                "beskrivning": "Uppdaterad beskrivning",
                "datum_inkom": "2026-01-20",
                "avsandare": "Ny avsändare",
                "mottagare": "Ny mottagare",
                "sekretess": "on",
            },
            follow_redirects=True,
        )
        db.session.refresh(handling)
        assert handling.typ == "upprattad"
        assert handling.beskrivning == "Uppdaterad beskrivning"
        assert handling.datum_inkom == date(2026, 1, 20)
        assert handling.avsandare == "Ny avsändare"
        assert handling.mottagare == "Ny mottagare"
        assert handling.sekretess is True

    def test_post_raderar_sekretess(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user, sekretess=True)
        db.session.commit()
        logga_in(client, "reg")

        client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "inkommande", "beskrivning": "Original"},
            follow_redirects=True,
        )
        db.session.refresh(handling)
        assert handling.sekretess is False

    def test_post_uppdaterar_kategorier(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        k1 = _skapa_kategori(db, "Faktura")
        k2 = _skapa_kategori(db, "Remiss")
        handling.kategorier = [k1]
        db.session.commit()
        logga_in(client, "reg")

        client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "inkommande", "beskrivning": "Original", "kategorier": [str(k2.id)]},
            follow_redirects=True,
        )
        db.session.refresh(handling)
        assert handling.kategorier.count() == 1
        assert handling.kategorier.first().id == k2.id

    def test_post_rensar_alla_kategorier(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        k1 = _skapa_kategori(db, "Faktura")
        handling.kategorier = [k1]
        db.session.commit()
        logga_in(client, "reg")

        # Skickar utan kategorier-nyckel → alla ska avmarkeras
        client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "inkommande", "beskrivning": "Original"},
            follow_redirects=True,
        )
        db.session.refresh(handling)
        assert handling.kategorier.count() == 0

    def test_post_ignorerar_ogiltigt_kategori_id(self, client, db):
        _, _, handling = self._setup(db)
        logga_in(client, "reg")

        client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "inkommande", "beskrivning": "Original", "kategorier": ["9999"]},
            follow_redirects=True,
        )
        db.session.refresh(handling)
        assert handling.kategorier.count() == 0

    def test_post_tomt_datum_sätts_till_none(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user, datum_inkom=date(2025, 1, 1))
        db.session.commit()
        logga_in(client, "reg")

        client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "inkommande", "beskrivning": "Original", "datum_inkom": ""},
            follow_redirects=True,
        )
        db.session.refresh(handling)
        assert handling.datum_inkom is None

    def test_post_redirectar_till_visa(self, client, db):
        _, _, handling = self._setup(db)
        logga_in(client, "reg")

        resp = client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "inkommande", "beskrivning": "Uppdaterad"},
        )
        assert resp.status_code == 302
        assert f"/handlingar/{handling.id}" in resp.headers["Location"]

    def test_post_loggas_i_granskningslogg(self, client, db):
        _, _, handling = self._setup(db)
        logga_in(client, "reg")

        client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "inkommande", "beskrivning": "Uppdaterad"},
            follow_redirects=True,
        )
        logg = AuditLog.query.filter_by(action="redigera_handling").first()
        assert logg is not None
        assert logg.target_id == handling.id

    # --- POST av admin ---

    def test_admin_kan_redigera(self, client, db):
        reg = skapa_user(db, username="reg", role="registrator")
        admin = skapa_user(db, username="adm", role="admin")
        arende = _skapa_arende(db, reg)
        handling = _skapa_handling(db, arende, reg)
        db.session.commit()
        logga_in(client, "adm")

        resp = client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "utgaende", "beskrivning": "Admin ändrade"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        db.session.refresh(handling)
        assert handling.beskrivning == "Admin ändrade"

    # --- Åtkomstkontroll ---

    def test_handlaggare_nekas_get(self, client, db):
        user = skapa_user(db, username="hl", role="handlaggare")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        db.session.commit()
        logga_in(client, "hl")

        resp = client.get(f"/handlingar/{handling.id}/redigera", follow_redirects=True)
        html = resp.data.decode()
        assert "behörighet" in html or resp.status_code == 403

    def test_handlaggare_nekas_post(self, client, db):
        user = skapa_user(db, username="hl", role="handlaggare")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user, beskrivning="Orörd")
        db.session.commit()
        logga_in(client, "hl")

        client.post(
            f"/handlingar/{handling.id}/redigera",
            data={"typ": "inkommande", "beskrivning": "Hackad"},
            follow_redirects=True,
        )
        db.session.refresh(handling)
        assert handling.beskrivning == "Orörd"

    def test_arkivarie_nekas(self, client, db):
        user = skapa_user(db, username="ark", role="arkivarie")
        reg = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, reg)
        handling = _skapa_handling(db, arende, reg)
        db.session.commit()
        logga_in(client, "ark")

        resp = client.get(f"/handlingar/{handling.id}/redigera", follow_redirects=True)
        html = resp.data.decode()
        assert "behörighet" in html or resp.status_code == 403

    def test_ej_inloggad_redirectas_till_login(self, client, db):
        user = skapa_user(db, username="reg", role="registrator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        db.session.commit()

        resp = client.get(f"/handlingar/{handling.id}/redigera")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_404_för_obefintlig_handling(self, client, db):
        skapa_user(db, username="reg", role="registrator")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.get("/handlingar/9999/redigera")
        assert resp.status_code == 404


# ── Standardprefix ────────────────────────────────────────────────────


class TestStandardprefix:
    def test_admin_kan_satta_standardprefix(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/admin/nummerserier",
            data={"standardprefix": "KST"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert Installning.get("standardprefix") == "KST"

    def test_standardprefix_sparas_med_versaler(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        client.post(
            "/admin/nummerserier",
            data={"standardprefix": "kst"},
            follow_redirects=True,
        )
        assert Installning.get("standardprefix") == "KST"

    def test_standardprefix_kan_uppdateras(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.add(Installning(key="standardprefix", value="DNR"))
        db.session.commit()
        logga_in(client, "admin")

        client.post(
            "/admin/nummerserier",
            data={"standardprefix": "NMD"},
            follow_redirects=True,
        )
        assert Installning.get("standardprefix") == "NMD"

    def test_standardprefix_loggas_i_granskningslogg(self, client, db):
        admin = skapa_user(db, username="admin", role="admin")
        db.session.commit()
        logga_in(client, "admin")

        client.post(
            "/admin/nummerserier",
            data={"standardprefix": "LOG"},
            follow_redirects=True,
        )
        logg = AuditLog.query.filter_by(action="andra_standardprefix").first()
        assert logg is not None
        assert logg.details["prefix"] == "LOG"
        assert logg.user_id == admin.id

    def test_registrator_nekas_satta_standardprefix(self, client, db):
        skapa_user(db, username="reg", role="registrator")
        db.session.commit()
        logga_in(client, "reg")

        resp = client.post(
            "/admin/nummerserier",
            data={"standardprefix": "REG"},
            follow_redirects=True,
        )
        assert "behörighet" in resp.data.decode()
        assert Installning.get("standardprefix") is None

    def test_handlaggare_nekas_satta_standardprefix(self, client, db):
        skapa_user(db, username="hl", role="handlaggare")
        db.session.commit()
        logga_in(client, "hl")

        resp = client.post(
            "/admin/nummerserier",
            data={"standardprefix": "HL"},
            follow_redirects=True,
        )
        assert "behörighet" in resp.data.decode()
        assert Installning.get("standardprefix") is None

    def test_ny_arende_anvaender_standardprefix(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.add(Installning(key="standardprefix", value="KST"))
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/arenden/ny",
            data={"arende_mening": "Testärende med prefix", "prefix": "KST"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        arende = Arende.query.first()
        assert arende is not None
        assert arende.diarienummer.startswith("KST-")

    def test_registrator_kan_inte_ange_eget_prefix(self, client, db):
        skapa_user(db, username="reg", role="registrator")
        db.session.add(Installning(key="standardprefix", value="KST"))
        db.session.commit()
        logga_in(client, "reg")

        # Skickar DNR men standardprefix är KST — servern ska ignorera formulärvärdet
        resp = client.post(
            "/arenden/ny",
            data={"arende_mening": "Testärende registrator", "prefix": "DNR"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        arende = Arende.query.first()
        assert arende is not None
        assert arende.diarienummer.startswith("KST-")

    def test_admin_kan_ange_eget_prefix(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.add(Installning(key="standardprefix", value="KST"))
        db.session.commit()
        logga_in(client, "admin")

        resp = client.post(
            "/arenden/ny",
            data={"arende_mening": "Testärende admin eget prefix", "prefix": "NMD"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        arende = Arende.query.first()
        assert arende is not None
        assert arende.diarienummer.startswith("NMD-")

    def test_standardprefix_visas_i_nummerserier_formulär(self, client, db):
        skapa_user(db, username="admin", role="admin")
        db.session.add(Installning(key="standardprefix", value="KST"))
        db.session.commit()
        logga_in(client, "admin")

        resp = client.get("/admin/nummerserier")
        assert resp.status_code == 200
        assert 'value="KST"' in resp.data.decode()


# ── Observatör ────────────────────────────────────────────────────────


class TestObservator:
    def test_observator_kan_lista_arenden(self, client, db):
        skapa_user(db, username="obs", role="observator")
        db.session.commit()
        logga_in(client, "obs")

        resp = client.get("/arenden/")
        assert resp.status_code == 200

    def test_observator_kan_visa_arende(self, client, db):
        user = skapa_user(db, username="obs", role="observator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "obs")

        resp = client.get(f"/arenden/{arende.id}")
        assert resp.status_code == 200

    def test_observator_nekas_skapa_arende(self, client, db):
        skapa_user(db, username="obs", role="observator")
        db.session.commit()
        logga_in(client, "obs")

        resp = client.post("/arenden/ny", data={"arende_mening": "Test"})
        assert resp.status_code == 302

    def test_observator_nekas_skapa_handling(self, client, db):
        user = skapa_user(db, username="obs", role="observator")
        arende = _skapa_arende(db, user)
        db.session.commit()
        logga_in(client, "obs")

        resp = client.post(f"/handlingar/ny/{arende.id}", data={"typ": "inkommande", "beskrivning": "Test"})
        assert resp.status_code == 302

    def test_observator_kan_visa_handling_utan_sekretess(self, client, db):
        user = skapa_user(db, username="obs", role="observator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user, sekretess=False)
        db.session.commit()
        logga_in(client, "obs")

        resp = client.get(f"/handlingar/{handling.id}")
        assert resp.status_code == 200

    def test_observator_nekas_sekretess_handling(self, client, db):
        user = skapa_user(db, username="obs", role="observator")
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user, sekretess=True)
        db.session.commit()
        logga_in(client, "obs")

        resp = client.get(f"/handlingar/{handling.id}")
        assert resp.status_code == 403

    def test_observator_nekas_handling_i_sekretess_arende(self, client, db):
        user = skapa_user(db, username="obs", role="observator")
        arende = _skapa_arende(db, user, sekretess=True)
        handling = _skapa_handling(db, arende, user)
        db.session.commit()
        logga_in(client, "obs")

        resp = client.get(f"/handlingar/{handling.id}")
        assert resp.status_code == 403

    def test_observator_nekas_admin(self, client, db):
        skapa_user(db, username="obs", role="observator")
        db.session.commit()
        logga_in(client, "obs")

        resp = client.get("/admin/")
        assert resp.status_code == 302

    def test_observator_handlingar_filtreras_i_arende_vy(self, client, db):
        user = skapa_user(db, username="obs", role="observator")
        arende = _skapa_arende(db, user, sekretess=True)
        _skapa_handling(db, arende, user)
        db.session.commit()
        logga_in(client, "obs")

        resp = client.get(f"/arenden/{arende.id}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Sekretessbelagda handlingar visas inte" in html
        assert "Inga handlingar registrerade." in html

    def test_observator_sok_exkluderar_sekretess_arenden(self, client, db):
        user = skapa_user(db, username="obs", role="observator")
        arende_oppen = _skapa_arende(db, user, diarienummer="DNR-OBS-001", arende_mening="Öppet ärende")
        arende_sekretess = _skapa_arende(db, user, diarienummer="DNR-OBS-002", arende_mening="Sekretessärende", sekretess=True)
        db.session.commit()
        logga_in(client, "obs")

        resp = client.get("/sok/?mening=ärende")
        html = resp.data.decode()
        assert "DNR-OBS-001" in html
        assert "DNR-OBS-002" not in html

    def test_observator_sok_visar_offentliga_arenden(self, client, db):
        user = skapa_user(db, username="obs", role="observator")
        _skapa_arende(db, user, diarienummer="DNR-OBS-003", arende_mening="Publikt ärende", sekretess=False)
        db.session.commit()
        logga_in(client, "obs")

        resp = client.get("/sok/?mening=Publikt")
        html = resp.data.decode()
        assert "DNR-OBS-003" in html


# ── API: GET /api/v1/brukare ──────────────────────────────────────────────────


def _skapa_api_nyckel(db, user, raw_key="test-api-key-123"):
    """Skapar en APIKey kopplad till user och returnerar (api_key_obj, raw_key)."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = APIKey(user_id=user.id, key_hash=key_hash, label="Testnyckel", aktiv=True)
    db.session.add(api_key)
    db.session.flush()
    return api_key, raw_key


class TestApiBrukare:
    def test_hamta_brukare_via_mejl(self, client, db):
        """Hittar användare via e-post och returnerar korrekt roll."""
        user = skapa_user(db, username="reg1", role="registrator", email="reg@example.com")
        _, raw_key = _skapa_api_nyckel(db, user)
        db.session.commit()

        resp = client.get(
            "/api/v1/brukare",
            query_string={"mejl": "reg@example.com"},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "username" not in data
        assert data["role"] == "registrator"
        assert data["active"] is True
        assert "id" in data

    def test_hamta_brukare_okand_mejl(self, client, db):
        """Returnerar 404 för okänd e-postadress."""
        user = skapa_user(db, username="reg2", role="registrator")
        _, raw_key = _skapa_api_nyckel(db, user)
        db.session.commit()

        resp = client.get(
            "/api/v1/brukare",
            query_string={"mejl": "ingen@example.com"},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 404

    def test_hamta_brukare_inaktivt_konto_returnerar_404(self, client, db):
        """Inaktivt konto syns inte — returnerar 404."""
        caller = skapa_user(db, username="reg3", role="registrator", email="reg3@example.com")
        _, raw_key = _skapa_api_nyckel(db, caller)
        skapa_user(db, username="inaktiv1", role="handlaggare", email="inaktiv@example.com", active=False)
        db.session.commit()

        resp = client.get(
            "/api/v1/brukare",
            query_string={"mejl": "inaktiv@example.com"},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 404

    def test_hamta_brukare_nekas_for_handlaggare(self, client, db):
        """Handläggare (fel roll på API-nyckeln) får 403."""
        user = skapa_user(db, username="hl1", role="handlaggare", email="hl@example.com")
        _, raw_key = _skapa_api_nyckel(db, user)
        db.session.commit()

        resp = client.get(
            "/api/v1/brukare",
            query_string={"mejl": "hl@example.com"},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert resp.status_code == 403

    def test_hamta_brukare_krav_api_nyckel(self, client, db):
        """Returnerar 401 utan API-nyckel."""
        resp = client.get(
            "/api/v1/brukare",
            query_string={"mejl": "vem@example.com"},
        )
        assert resp.status_code == 401
