# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is DFS2?

DFS2 är ett diarieföringssystem och ärendehanteringssystem byggt i enlighet med Offentlighets- och sekretesslagen (OSL) och Arkivlagen. Det hanterar registrering av ärenden, handlingar och dokument med fullständig spårbarhet via granskningslogg.

## Commands

### Setup (local development / testing)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
sudo apt install libmagic1  # Required system library
```

### Run tests

```bash
source .venv/bin/activate
pytest tests/                  # All tests (uses SQLite in-memory, no Docker needed)
pytest tests/test_models.py    # Unit tests for models only
pytest tests/test_routes.py    # Integration tests for routes only
pytest tests/ -v               # Verbose output
```

### Run application (production/staging)

```bash
docker compose up --build -d   # Start (builds image, runs in background)
docker compose ps              # Check status
docker compose logs app        # View logs
docker compose down            # Stop
docker compose down -v         # Stop and delete database volume
```

Before first start, set up secrets:
```bash
cp secrets/db_password.txt.example    secrets/db_password.txt
cp secrets/secret_key.txt.example     secrets/secret_key.txt
cp secrets/admin_password.txt.example secrets/admin_password.txt
python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/secret_key.txt
```

## Architecture

### Stack

- **Flask** + **SQLAlchemy** + **PostgreSQL** (production) / SQLite (tests)
- **flask-login** for session management, **flask-wtf** for CSRF, **flask-limiter** for rate limiting
- **Gunicorn** as WSGI server in Docker
- File storage: binary blobs in the database (`DocumentVersion.fildata`)

### App structure

```
app/__init__.py       — create_app() factory, registers all blueprints and security middleware
app/models.py         — All SQLAlchemy models and helper functions
app/auth.py           — auth_bp: login/logout/dashboard, role_required() decorator
app/routes/arenden.py — arenden_bp (/arenden): case management
app/routes/handlingar.py — handlingar_bp: document records attached to cases
app/routes/sok.py     — sok_bp (/sok): search
app/routes/admin.py   — admin_bp (/admin): user management, audit log, number series
app/routes/arkiv.py   — arkiv_bp (/arkiv): archive view and JSON export
config.py             — Config class, reads secrets from env vars or /run/secrets/ (Docker Secrets)
seed.py               — Creates initial admin user (run once on first deploy)
```

### Data model

- **Arende** (case): has a unique `diarienummer` (auto-generated via `Nummerserie`), a `status` with enforced transitions, soft-delete (`deleted=True`)
- **Handling** (document record): belongs to an Arende, has type (`inkommande`/`utgaende`/`upprattad`), soft-delete
- **DocumentVersion**: each Handling can have multiple file versions; file binary stored in `fildata`
- **AuditLog**: every significant action is logged with user, action type, target, IP address — call `log_action()` then commit
- **Nummerserie**: generates diarienumbers like `DNR-2026-0001`; `Nummerserie.next_number(prefix)` is called within a transaction before commit

### Access control

The `role_required(*roles)` decorator in `app/auth.py` wraps `@login_required` and checks `current_user.role`. Four roles exist: `admin`, `registrator`, `handlaggare`, `arkivarie`. Always apply `role_required` or `login_required` to route handlers.

### Security notes

- Secrets are loaded from Docker Secrets files (`/run/secrets/`) or env vars — never hardcoded
- PostgreSQL `statement_timeout` is set per connection (see `_registrera_fragetimeout` in `__init__.py`) to limit query duration
- Login attempts are rate-limited; accounts lock after 5 failed attempts for 15 minutes
- `SESSION_COOKIE_SECURE` defaults to `true` — set to `false` only in local non-HTTPS dev
- CSP and other security headers are added in `after_request` hook in `create_app()`

### Configuration

`config.py` reads from environment variables and Docker Secrets. Key vars: `DATABASE_URL`, `POSTGRES_USER`, `POSTGRES_DB`, `PROXY_COUNT` (set to 1 when behind reverse proxy), `DB_QUERY_TIMEOUT_MS`. See `.env.example` for full list.
