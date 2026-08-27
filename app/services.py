"""Tjänstefunktioner för återanvändbar affärslogik (mjuk borttagning m.m.)."""

from datetime import date

from flask import abort

from app.models import Arende, Handling


def hamta_aktivt_arende(arende_id):
    """Hämta ett ärende som inte är mjuk-borttaget, annars 404."""
    arende = Arende.query.get_or_404(arende_id)
    if arende.deleted:
        abort(404)
    return arende


def hamta_aktiv_handling(handling_id):
    """Hämta en handling som inte är mjuk-borttaget, annars 404."""
    handling = Handling.query.get_or_404(handling_id)
    if handling.deleted:
        abort(404)
    return handling


def _parse_datum(varde) -> date | None:
    """Parsar en ISO-datumsträng säkert. Returnerar None vid ogiltigt format."""
    try:
        return date.fromisoformat(varde)
    except (ValueError, TypeError):
        return None


def _handling_sekretess_filter(q, user):
    """Begränsa en Handling-query så att sekretesshandlingar bara
    inkluderas om användaren har rätt att se dem."""
    from sqlalchemy import or_

    if user.role in ("admin", "registrator"):
        return q  # får se alla
    if user.role == "handlaggare":
        # Bara egna ärendens sekretesshandlingar
        return q.filter(
            or_(
                Handling.sekretess == False,  # noqa: E712
                Handling.arende.has(Arende.handlaggare_id == user.id),
            )
        ).filter(
            or_(
                Handling.arende.has(Arende.sekretess == False),  # noqa: E712
                Handling.arende.has(Arende.handlaggare_id == user.id),
            )
        )
    if user.role == "arkivarie":
        return q.filter(
            or_(
                Handling.sekretess == False,  # noqa: E712
                Handling.arende.has(Arende.status == "arkiverat"),
            )
        ).filter(
            or_(
                Handling.arende.has(Arende.sekretess == False),  # noqa: E712
                Handling.arende.has(Arende.status == "arkiverat"),
            )
        )
    # observator och övriga: bara icke-sekretess i icke-sekretess-ärenden
    return q.filter(
        Handling.sekretess == False,  # noqa: E712
        Handling.arende.has(Arende.sekretess == False),  # noqa: E712
    )


def sok_arenden(
    user,
    *,
    diarienummer=None,
    mening=None,
    status=None,
    fran=None,
    till=None,
    avsandare=None,
    beskrivning=None,
    kategori_id=None,
    limit=100,
):
    """Bygg en sekretessfiltrerad Arende-query enligt sökparametrar.

    Applicerar Arende.sekretess_filter automatiskt. Returnerar en lista
    med Arende-objekt.
    """
    query = Arende.query.filter_by(deleted=False)
    query = Arende.sekretess_filter(query, user)

    if diarienummer:
        query = query.filter(Arende.diarienummer.ilike(f"%{diarienummer[:100]}%"))
    if mening:
        query = query.filter(Arende.arende_mening.ilike(f"%{mening[:100]}%"))
    if status and status in Arende.STATUS_LABELS:
        query = query.filter_by(status=status)
    if fran:
        query = query.filter(Arende.skapad_datum >= fran)
    if till:
        query = query.filter(Arende.skapad_datum <= till)

    if avsandare or beskrivning or kategori_id:
        sub = Handling.query.filter(Handling.deleted == False)  # noqa: E712
        sub = _handling_sekretess_filter(sub, user)
        if avsandare:
            sub = sub.filter(Handling.avsandare.ilike(f"%{avsandare[:100]}%"))
        if beskrivning:
            sub = sub.filter(Handling.beskrivning.ilike(f"%{beskrivning[:100]}%"))
        if kategori_id:
            from app.models import handling_kategori

            sub = sub.join(handling_kategori).filter(
                handling_kategori.c.kategori_id == kategori_id
            )
        arende_ids = sub.with_entities(Handling.arende_id).distinct()
        query = query.filter(Arende.id.in_(arende_ids))

    return query.order_by(Arende.skapad_datum.desc()).limit(limit).all()