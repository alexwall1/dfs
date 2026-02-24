# Köra tester

Testerna körs med pytest och använder SQLite i minnet — ingen PostgreSQL eller Docker behövs.

## Förutsättningar

Installera testberoenden i en virtuell miljö:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
```

På Ubuntu/Debian krävs även systembiblioteket för `python-magic`:

```bash
sudo apt install libmagic1
```

## Kör alla tester

```bash
source .venv/bin/activate
pytest tests/
```

## Kör specifik testfil

```bash
pytest tests/test_models.py   # Enhetstester för databasmodeller
pytest tests/test_routes.py   # Integrationstester för routes/vyer
```

## Kör med utförlig utskrift

```bash
pytest tests/ -v
```

## Teststruktur

| Fil | Innehåll |
|-----|----------|
| `tests/conftest.py` | Fixtures: Flask-app (SQLite), databas, testklient |
| `tests/test_models.py` | Enhetstester för User, Arende, Handling, AuditLog, Nummerserie m.m. |
| `tests/test_routes.py` | Integrationstester för alla routes (login, ärenden, handlingar, sök, admin, arkiv) |
