import logging
import os

import httpx

logger = logging.getLogger(__name__)

DFS2_BASE_URL = os.environ.get("DFS2_BASE_URL", "http://app:5000")


def _get_api_key() -> str:
    """Läser DFS2 API-nyckeln från Docker Secret eller miljövariabel."""
    try:
        with open("/run/secrets/dfs2_api_key") as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.environ.get("DFS2_API_KEY", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_get_api_key()}"}


def hamta_anvandare_via_mejl(email: str) -> dict | None:
    """
    Hämtar en DFS2-användare via e-postadress.
    Returnerar {id, username, role, active} eller None vid 404.
    """
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(
            f"{DFS2_BASE_URL}/api/v1/brukare",
            headers=_headers(),
            params={"mejl": email},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


def hamta_arende_via_id(arende_id: int) -> dict | None:
    """
    Hämtar ett ärende via dess ID.
    Returnerar ärendedict (inkl. handlaggare_id) eller None vid 404.
    """
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(
            f"{DFS2_BASE_URL}/api/v1/arenden/{arende_id}",
            headers=_headers(),
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


def hamta_arende_via_diarienummer(diarienummer: str) -> dict | None:
    """
    Söker igenom alla ärenden för att hitta ett med givet diarienummer.
    DFS2 API saknar filter på diarienummer, så vi paginerar över alla sidor.
    Returnerar ärendet som dict, eller None om det inte hittas.
    """
    page = 1
    with httpx.Client(timeout=30.0) as client:
        while True:
            resp = client.get(
                f"{DFS2_BASE_URL}/api/v1/arenden",
                headers=_headers(),
                params={"page": page},
            )
            resp.raise_for_status()
            data = resp.json()
            for arende in data.get("arenden", []):
                if arende.get("diarienummer") == diarienummer:
                    return arende
            if page >= data.get("sidor", 1):
                break
            page += 1
    return None


def skapa_handling(
    arende_id: int,
    typ: str,
    beskrivning: str,
    datum_inkom: str | None = None,
    avsandare: str | None = None,
    mottagare: str | None = None,
    sekretess: bool = False,
    fil_data: bytes | None = None,
    fil_namn: str | None = None,
    fil_mime: str | None = None,
    registrerad_av_id: int | None = None,
) -> dict:
    """Skapar en ny handling på ett ärende via DFS2 REST API (multipart/form-data)."""
    form_data: dict = {
        "typ": typ,
        "beskrivning": beskrivning,
        "sekretess": "true" if sekretess else "false",
    }
    if datum_inkom:
        form_data["datum_inkom"] = datum_inkom
    if avsandare:
        form_data["avsandare"] = avsandare
    if mottagare:
        form_data["mottagare"] = mottagare
    if registrerad_av_id is not None:
        form_data["registrerad_av_id"] = str(registrerad_av_id)

    files = None
    if fil_data and fil_namn:
        files = {"fil": (fil_namn, fil_data, fil_mime or "application/octet-stream")}

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{DFS2_BASE_URL}/api/v1/arenden/{arende_id}/handlingar",
            headers=_headers(),
            data=form_data,
            files=files,
        )
        resp.raise_for_status()
        return resp.json()


def ladda_upp_version(
    handling_id: int,
    fil_data: bytes,
    fil_namn: str,
    fil_mime: str | None = None,
    kommentar: str | None = None,
) -> dict:
    """Laddar upp ytterligare en filversion till en befintlig handling."""
    form_data: dict = {}
    if kommentar:
        form_data["kommentar"] = kommentar

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{DFS2_BASE_URL}/api/v1/handlingar/{handling_id}/versioner",
            headers=_headers(),
            data=form_data,
            files={"fil": (fil_namn, fil_data, fil_mime or "application/octet-stream")},
        )
        resp.raise_for_status()
        return resp.json()
