import os
import secrets


def _las_hemlighet(env_var: str, secret_namn: str | None = None) -> str | None:
    """Läser värde från miljövariabel, med fallback till Docker Secret-fil."""
    val = os.environ.get(env_var)
    if val:
        return val
    fil = f"/run/secrets/{secret_namn or env_var.lower()}"
    try:
        with open(fil) as f:
            return f.read().strip()
    except OSError:
        return None


def _bygg_database_url() -> str:
    """
    Returnerar DATABASE_URL. Om miljövariabeln DATABASE_URL är satt används den.
    Annars byggs URL:en från POSTGRES_*-variabler med lösenord från Docker Secret.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "dfs")
    password = _las_hemlighet("POSTGRES_PASSWORD", "db_password")
    host = os.environ.get("DB_HOST", "db")
    port = os.environ.get("DB_PORT", "5432")
    dbname = os.environ.get("POSTGRES_DB", "dfs")
    if not password:
        raise RuntimeError(
            "Databaslösenord saknas: sätt DATABASE_URL eller "
            "POSTGRES_PASSWORD / secrets/db_password.txt"
        )
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


class Config:
    SECRET_KEY = _las_hemlighet("SECRET_KEY", "secret_key") or secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI = _bygg_database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB upload limit

    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Antal betrodda reverse proxies framför appen.
    # Sätt till 1 (eller fler) om appen körs bakom nginx/Caddy/load balancer
    # så att X-Forwarded-For hanteras korrekt i granskningsloggen.
    PROXY_COUNT = int(os.environ.get("PROXY_COUNT", "0"))

    # Maximal körtid i millisekunder för databasfrågor (PostgreSQL statement_timeout).
    # Skyddar mot DoS via tunga sökfrågor. 0 = ingen gräns.
    DB_QUERY_TIMEOUT_MS = int(os.environ.get("DB_QUERY_TIMEOUT_MS", "5000"))
