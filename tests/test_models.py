"""Tester för databasmodellerna."""

from datetime import datetime, timezone, date

import pytest

from app.models import (
    User,
    Arende,
    Handling,
    DocumentVersion,
    AuditLog,
    Nummerserie,
    log_action,
    validera_losenord,
)


# ── Hjälpfunktioner ──────────────────────────────────────────────────


def _skapa_user(db, **kw):
    password = kw.pop("password", "lösenord123")
    defaults = dict(
        username="testuser",
        full_name="Test Testsson",
        role="handlaggare",
    )
    defaults.update(kw)
    user = User(**defaults)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    return user


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


# ── User ─────────────────────────────────────────────────────────────


class TestUser:
    def test_set_and_check_password(self, db):
        user = _skapa_user(db, password="hemligt")
        assert user.check_password("hemligt")
        assert not user.check_password("felaktigt")

    def test_password_hash_not_plaintext(self, db):
        user = _skapa_user(db, password="hemligt")
        assert user.password_hash != "hemligt"

    def test_default_role(self, db):
        user = _skapa_user(db)
        assert user.role == "handlaggare"

    def test_role_label(self, db):
        for role, label in User.ROLE_LABELS.items():
            user = _skapa_user(db, username=f"user_{role}", role=role)
            assert user.role_label == label

    def test_is_active_default_true(self, db):
        user = _skapa_user(db)
        assert user.is_active is True

    def test_is_active_false(self, db):
        user = _skapa_user(db, active=False)
        assert user.is_active is False

    def test_created_at_auto(self, db):
        user = _skapa_user(db)
        assert user.created_at is not None

    def test_unique_username(self, db):
        _skapa_user(db, username="dubblett")
        with pytest.raises(Exception):
            _skapa_user(db, username="dubblett")
            db.session.flush()

    def test_role_label_unknown_role(self, db):
        user = _skapa_user(db, role="okand")
        assert user.role_label == "okand"


# ── Arende ───────────────────────────────────────────────────────────


class TestArende:
    def test_default_status(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        assert arende.status == "oppnat"

    def test_status_label(self, db):
        user = _skapa_user(db)
        for status, label in Arende.STATUS_LABELS.items():
            arende = _skapa_arende(
                db,
                user,
                diarienummer=f"DNR-{status}",
                status=status,
            )
            assert arende.status_label == label

    def test_allowed_transitions_oppnat(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user, status="oppnat")
        assert arende.allowed_transitions == ["pagaende"]

    def test_allowed_transitions_pagaende(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user, status="pagaende")
        assert arende.allowed_transitions == ["avslutat"]

    def test_allowed_transitions_avslutat(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user, status="avslutat")
        assert set(arende.allowed_transitions) == {"arkiverat", "pagaende"}

    def test_allowed_transitions_arkiverat_empty(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user, status="arkiverat")
        assert arende.allowed_transitions == []

    def test_soft_delete(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        assert arende.deleted is False

        arende.deleted = True
        db.session.flush()

        # Filtrerad fråga ska inte hitta det
        result = Arende.query.filter_by(deleted=False).all()
        assert arende not in result

    def test_sekretess_default_false(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        assert arende.sekretess is False

    def test_unique_diarienummer(self, db):
        user = _skapa_user(db)
        _skapa_arende(db, user, diarienummer="DNR-UNIK-001")
        with pytest.raises(Exception):
            _skapa_arende(db, user, diarienummer="DNR-UNIK-001")
            db.session.flush()

    def test_skapad_datum_auto(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        assert arende.skapad_datum is not None

    def test_handlaggare_relation(self, db):
        skapare = _skapa_user(db, username="skapare")
        handlaggare = _skapa_user(db, username="handlaggare1")
        arende = _skapa_arende(db, skapare, handlaggare_id=handlaggare.id)
        assert arende.handlaggare.id == handlaggare.id
        assert arende.skapare.id == skapare.id

    def test_andrad_datum_auto(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        assert arende.andrad_datum is not None

    def test_handlingar_relation(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        _skapa_handling(db, arende, user, beskrivning="H1")
        _skapa_handling(db, arende, user, beskrivning="H2")
        assert arende.handlingar.count() == 2

    def test_status_label_unknown(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user, status="okand")
        assert arende.status_label == "okand"


# ── Handling ─────────────────────────────────────────────────────────


class TestHandling:
    def test_typ_labels(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        for typ, label in Handling.TYP_LABELS.items():
            h = _skapa_handling(db, arende, user, typ=typ)
            assert h.typ_label == label

    def test_soft_delete(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        assert handling.deleted is False

        handling.deleted = True
        db.session.flush()

        result = Handling.query.filter_by(deleted=False).all()
        assert handling not in result

    def test_arende_relation(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        assert handling.arende.id == arende.id

    def test_datum_inkom(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        d = date(2026, 1, 15)
        handling = _skapa_handling(db, arende, user, datum_inkom=d)
        assert handling.datum_inkom == d

    def test_sekretess_default_false(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        assert handling.sekretess is False

    def test_skapad_datum_auto(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        assert handling.skapad_datum is not None

    def test_skapare_relation(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        assert handling.skapare.id == user.id

    def test_avsandare_mottagare_optional(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        assert handling.avsandare is None
        assert handling.mottagare is None


# ── DocumentVersion ──────────────────────────────────────────────────


class TestDocumentVersion:
    def test_create_version(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)

        v = DocumentVersion(
            handling_id=handling.id,
            version_nr=1,
            filnamn="rapport.pdf",
            fildata=b"PDF-data",
            mime_type="application/pdf",
            skapad_av=user.id,
        )
        db.session.add(v)
        db.session.flush()

        assert v.filnamn == "rapport.pdf"
        assert v.fildata == b"PDF-data"
        assert v.version_nr == 1

    def test_multiple_versions(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)

        for nr in (1, 2, 3):
            v = DocumentVersion(
                handling_id=handling.id,
                version_nr=nr,
                filnamn=f"dok_v{nr}.pdf",
                fildata=b"data",
                skapad_av=user.id,
            )
            db.session.add(v)
        db.session.flush()

        versioner = handling.versioner.all()
        assert len(versioner) == 3
        assert [v.version_nr for v in versioner] == [1, 2, 3]

    def test_skapad_datum_auto(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        v = DocumentVersion(
            handling_id=handling.id,
            version_nr=1,
            filnamn="test.pdf",
            fildata=b"data",
            skapad_av=user.id,
        )
        db.session.add(v)
        db.session.flush()
        assert v.skapad_datum is not None

    def test_kommentar_optional(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        v = DocumentVersion(
            handling_id=handling.id,
            version_nr=1,
            filnamn="test.pdf",
            fildata=b"data",
            skapad_av=user.id,
        )
        db.session.add(v)
        db.session.flush()
        assert v.kommentar is None

    def test_handling_relation(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        v = DocumentVersion(
            handling_id=handling.id,
            version_nr=1,
            filnamn="test.pdf",
            fildata=b"data",
            skapad_av=user.id,
        )
        db.session.add(v)
        db.session.flush()
        assert v.handling.id == handling.id

    def test_skapare_relation(self, db):
        user = _skapa_user(db)
        arende = _skapa_arende(db, user)
        handling = _skapa_handling(db, arende, user)
        v = DocumentVersion(
            handling_id=handling.id,
            version_nr=1,
            filnamn="test.pdf",
            fildata=b"data",
            skapad_av=user.id,
        )
        db.session.add(v)
        db.session.flush()
        assert v.skapare.id == user.id


# ── AuditLog ─────────────────────────────────────────────────────────


class TestAuditLog:
    def test_log_action(self, app, db):
        user = _skapa_user(db)
        with app.test_request_context():
            log_action(
                user_id=user.id,
                action="skapa",
                target_type="arende",
                target_id=1,
                details={"info": "test"},
            )
            db.session.flush()

        entry = AuditLog.query.first()
        assert entry.action == "skapa"
        assert entry.target_type == "arende"
        assert entry.details == {"info": "test"}
        assert entry.user_id == user.id

    def test_log_action_timestamp(self, app, db):
        user = _skapa_user(db)
        with app.test_request_context():
            log_action(user_id=user.id, action="test")
            db.session.flush()

        entry = AuditLog.query.first()
        assert entry.timestamp is not None

    def test_log_action_ip_address(self, app, db):
        user = _skapa_user(db)
        with app.test_request_context(environ_base={"REMOTE_ADDR": "192.168.1.1"}):
            log_action(user_id=user.id, action="test_ip")
            db.session.flush()

        entry = AuditLog.query.first()
        assert entry.ip_address == "192.168.1.1"

    def test_log_action_without_optional_fields(self, app, db):
        user = _skapa_user(db)
        with app.test_request_context():
            log_action(user_id=user.id, action="minimal")
            db.session.flush()

        entry = AuditLog.query.first()
        assert entry.target_type is None
        assert entry.target_id is None
        assert entry.details is None

    def test_user_relation(self, app, db):
        user = _skapa_user(db)
        with app.test_request_context():
            log_action(user_id=user.id, action="test_rel")
            db.session.flush()

        entry = AuditLog.query.first()
        assert entry.user.id == user.id
        assert entry.user.username == "testuser"


# ── validera_losenord ────────────────────────────────────────────────


class TestValeraLosenord:
    def test_godkant_losenord(self):
        assert validera_losenord("Hemligt!Pass123") == []

    def test_exakt_12_tecken_godkant(self):
        # Exakt minimigränsen: H(upper) + emlig(lower) + !Pa(special) + 123(digit) = 12 tecken
        assert validera_losenord("Hemlig!Pa123") == []

    def test_for_kort_ger_fel(self):
        fel = validera_losenord("Kort!1A")
        assert any("12 tecken" in f for f in fel)

    def test_for_kort_under_12_ger_fel(self):
        fel = validera_losenord("Hemli!1")  # 7 tecken
        assert any("12 tecken" in f for f in fel)

    def test_saknar_versal_ger_fel(self):
        fel = validera_losenord("hemligt!pass123")
        assert any("versal" in f for f in fel)

    def test_saknar_gemen_ger_fel(self):
        fel = validera_losenord("HEMLIGT!PASS123")
        assert any("gemen" in f for f in fel)

    def test_saknar_siffra_ger_fel(self):
        fel = validera_losenord("Hemligt!PassABC")
        assert any("siffra" in f for f in fel)

    def test_saknar_specialtecken_ger_fel(self):
        fel = validera_losenord("HemligtPass1234")
        assert any("specialtecken" in f for f in fel)

    def test_tom_strang_ger_alla_fel(self):
        fel = validera_losenord("")
        assert len(fel) == 5

    def test_flera_brister_returnerar_flera_fel(self):
        # "kort" saknar: längd, versal, siffra, specialtecken → 4 fel
        fel = validera_losenord("kort")
        assert len(fel) == 4

    def test_bara_ett_fel_i_taget(self):
        # Har allt utom siffra → exakt ett fel
        fel = validera_losenord("Hemligt!PassABC")
        assert len(fel) == 1
        assert "siffra" in fel[0]

    def test_specialtecken_varianter_godkanda(self):
        for special in "!@#$%^&*":
            pwd = f"HemligtPass1{special}"
            assert validera_losenord(pwd) == [], f"'{special}' borde vara godkänt specialtecken"

    def test_returnerar_lista(self):
        assert isinstance(validera_losenord("Hemligt!Pass123"), list)
        assert isinstance(validera_losenord("svagt"), list)


# ── Nummerserie ──────────────────────────────────────────────────────


class TestNummerserie:
    def test_next_number_format(self, db):
        year = datetime.now(timezone.utc).year
        nummer = Nummerserie.next_number("DNR")
        assert nummer == f"DNR-{year}-0001"

    def test_next_number_increments(self, db):
        year = datetime.now(timezone.utc).year
        n1 = Nummerserie.next_number("DNR")
        n2 = Nummerserie.next_number("DNR")
        n3 = Nummerserie.next_number("DNR")
        assert n1 == f"DNR-{year}-0001"
        assert n2 == f"DNR-{year}-0002"
        assert n3 == f"DNR-{year}-0003"

    def test_different_prefix(self, db):
        year = datetime.now(timezone.utc).year
        Nummerserie.next_number("DNR")
        Nummerserie.next_number("DNR")
        ink = Nummerserie.next_number("INK")
        assert ink == f"INK-{year}-0001"

    def test_unique_constraint(self, db):
        s1 = Nummerserie(prefix="X", year=2026, current_number=1)
        db.session.add(s1)
        db.session.flush()

        s2 = Nummerserie(prefix="X", year=2026, current_number=2)
        db.session.add(s2)
        with pytest.raises(Exception):
            db.session.flush()
