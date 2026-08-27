# Implementationsplan – DFS2 Kodkvalitet & Robusthet (Uppgift 11–18)

Denna plan täcker lågprioriterade kodkvalitets- och robusthetsuppgifter.
Förutsätter att uppgift 1–10 är klara (särskilt `app/services.py` med
`hamta_aktivt_arende`/`hamta_aktiv_handling`, `Arende.sekretess_filter`,
och eventuell `_handling_sekretess_filter` från uppgift 6).

Dessa uppgifter är lågrisk-refaktoreringar som kan göras i valfri ordning,
men vissa har beroenden (noterade nedan). Syftet är att minska teknisk
skuld och eliminera 500-fel från ogiltig input, inte att lägga ny
funktionalitet.

## Sekvensordning

- **Oberoende (parallella):** 12, 13, 16, 17, 18.
- **Beroenden:** 11 (sök-service) bör göras efter uppgift 6 (som
  introducerar `_handling_sekretess_filter`). 14 (N+1) är oberoende men
  rör samma queries som 11 — gör 11 före 14. 15 (blobbar) är en större
  arkitekturändring; lägg den sist och överväg om den ska brytas ut till
  en egen plan.

---

## Uppgift 11 — Duplicerad söklogik (auth.dashboard vs sok.sok)

**Problem:** Roll-filtrerad ärendesök implementeras på två ställen med
olika nyanser: `app/auth.py:dashboard` (rad 132–232, särskilt
sökblocket 163–223) och `app/routes/sok.py:sok` (rad 26–109). Risk att
en felkorrigering bara görs på en plats. Uppgift 1 och 6 lägger till
`Arende.sekretess_filter` resp. `_handling_sekretess_filter` — det
förvärrar risken om dessa inte återanvänds konsekvent.

**Berörda filer:**
- `app/services.py` (ny hem för sök-service, skapad i uppgift 3)
- `app/routes/sok.py` — `sok` (rad 26–109)
- `app/auth.py` — `dashboard` sökblock (rad 163–223)

### Steg

1. **Skapa en sök-service** i `app/services.py`:
   ```python
   def sok_arenden(user, *, diarienummer=None, mening=None, status=None,
                   fran=None, till=None, avsandare=None, beskrivning=None,
                   kategori_id=None, limit=100):
       """Bygg en sekretessfiltrerad Arende-query enligt sökparametrar.
       Returnerar en lista med Arende-objekt (eller en query för vidare
       paginering). Applicerar Arende.sekretess_filter automatiskt."""
       from app.models import Arende, Handling, handling_kategori
       from sqlalchemy import or_

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
           sub = Handling.query.filter(Handling.deleted == False)
           sub = _handling_sekretess_filter(sub, user)
           if avsandare:
               sub = sub.filter(Handling.avsandare.ilike(f"%{avsandare[:100]}%"))
           if beskrivning:
               sub = sub.filter(Handling.beskrivning.ilike(f"%{beskrivning[:100]}%"))
           if kategori_id:
               sub = sub.join(handling_kategori).filter(
                   handling_kategori.c.kategori_id == kategori_id
               )
           arende_ids = sub.with_entities(Handling.arende_id).distinct()
           query = query.filter(Arende.id.in_(arende_ids))

       return query.order_by(Arende.skapad_datum.desc()).limit(limit).all()
   ```
   (`_handling_sekretess_filter` från uppgift 6 bör lyftas till
   `app/services.py` som en modul-funktion som tar `user` som parameter,
   inte läsa `current_user` globalt — det gör den testbar.)
2. **Refaktorera `sok.py:sok`** att anropa `sok_arenden(current_user, ...)`
   med parsade parametrar. Behåll datum-parsning och flash-meddelanden för
   ogiltig input i routen (service-funktionen tar `date`-objekt).
3. **Refaktorera `auth.py:dashboard`** sökblock (rad 163–223) att anropa
   `sok_arenden(current_user, mening=q, limit=50)`. Detta eliminerar
   ~60 rader duplicerad logik och gör dashboard-sökningen konsekvent med
   `/sok/`.
4. **Behåll `stats`/`senaste`/`mina_arenden`** i dashboard som egna queries
   (de har annan semantik än sök), men se till att de använder
   `Arende.sekretess_filter` från uppgift 1.

### Regressionsrisk
- Dashboard-sökningen har idag olika beteende per roll (t.ex. handläggare
  söker bara bland egna ärenden). `sok_arenden` med `Arende.sekretess_filter`
  ger handläggare synlighet till `sekretess == False OR handlaggare_id ==
  user.id`. Detta är **bredare** än dagens dashboard (som begränsar till
  agda_ids). Beslut: antingen behåll dashboardens snävare beteende genom
  en extra parameter `egna_bara=True`, eller harmonisera till
  `sekretess_filter`-beteendet. **Rekommendation:** harmonisera —
  handläggare bör kunna söka bland alla öppna ärenden, inte bara egna,
  för konsistens med `/sok/`. Dokumentera ändringen i release notes.

### Teststrategi
- Befintliga söktester och dashboard-tester ska fortsätta passera.
- Lägg till `test_dashboard_sok_ger_samma_resultat_som_sok_route` —
  samma sökord via `/dashboard?q=X` och `/sok/?mening=X` ger samma
  ärenden (för en given roll).

---

## Uppgift 12 — Ohanterade ValueError/KeyError → 500

**Problem:** Flera webb-routes använder `request.form["..."]` (KeyError
vid saknat fält) eller `date.fromisoformat(...)` (ValueError vid ogiltigt
datum) utan hantering, vilket ger 500-fel. API:et (`api.py`) gör rätt via
Marshmallow — följ det mönstret.

**Berörda filer:**
- `app/routes/arenden.py` — `ny` (rad 43 `request.form["arende_mening"]`),
  `redigera` (rad 98)
- `app/routes/handlingar.py` — `ny` (rad 122 `date.fromisoformat`,
  rad 126 `request.form["typ"]`, rad 130 `request.form["beskrivning"]`),
  `redigera` (rad 252, 253, 254), `ny_version`
- `app/routes/admin.py` — `ny_anvandare` (rad 31, 47), `redigera_anvandare`
  (rad 115, 117)

### Steg

1. **Ersätt `request.form["key"]` med `request.form.get("key", "").strip()`**
   + explicit validering:
   ```python
   arende_mening = request.form.get("arende_mening", "").strip()
   if not arende_mening:
       flash("Ärendemening är obligatorisk.", "danger")
       return redirect(url_for("arenden.ny"))
   ```
2. **Linda `date.fromisoformat` i try/except** (eller använd en hjälpare):
   ```python
   def _parse_datum(varde, field_name="datum"):
       if not varde:
           return None
       try:
           return date.fromisoformat(varde)
       except ValueError:
           flash(f"Ogiltigt datum för {field_name}: {varde}", "danger")
           return None  # caller beslutar om redirect
   ```
   Flytta `_parse_datum` från `sok.py` (rad 13–18) till `app/services.py`
   för återanvändning.
3. **Specifika ändringar:**
   - `arenden.py:ny` (rad 43): använd `.get()` + validering.
   - `arenden.py:redigera` (rad 98): samma.
   - `handlingar.py:ny` (rad 121–130): parse-datum + `.get()` för typ/
     beskrivning; validera `typ` mot `("inkommande", "utgaende", "upprattad")`.
   - `handlingar.py:redigera` (rad 251–257): samma.
   - `handlingar.py:ny_version`: `request.form.get("kommentar", "")` är
     redan säkert; inget att göra.
   - `admin.py:ny_anvandare` (rad 31, 47) och `redigera_anvandare`
     (rad 115, 117): `.get()` för full_name/email/role; validera role mot
     whitelist (se uppgift 13).
4. **Returnera formulärvyn med flash-variant vid valideringsfel** så
   användaren behåller sin input (rendera template igen, inte redirect).

### Regressionsrisk
- Minimal. Befintliga tester som skickar giltig data påverkas inte.
- Tester som skickar saknade fält och förväntar sig 500 måste uppdateras
  till att förvänta sig 200 (med flash) eller 302 (redirect). Sök efter
  sådana tester.

### Teststrategi (`tests/test_routes.py`)
- `test_ny_arende_utan_arende_mening_visar_fel` — POST utan
  `arende_mening` → 200 + flash "obligatorisk", inget ärende skapat.
- `test_ny_handling_ogiltigt_datum_visar_fel` — POST med
  `datum_inkom=foobar` → 200 + flash, ingen handling skapad.
- `test_ny_handling_ogiltig_typ_visar_fel` — POST med `typ=foobar`.
- `test_redigera_handling_saknad_beskrivning_visar_fel`.

---

## Uppgift 13 — `role`/`typ`-värden valideras inte

**Problem:** `admin.py:ny_anvandare` (rad 47) sätter `role=request.form["role"]`
utan whitelist. `admin.py:redigera_anvandare` (rad 117) samma. `handlingar.py:ny`
(rad 126) sätter `typ=request.form["typ"]` utan kontroll mot `TYP_LABELS`.
Slarvdata kan skapas. API:et validerar korrekt via Marshmallow.

**Berörda filer:** `app/routes/admin.py`, `app/routes/handlingar.py`,
ev. `app/models.py` (för konstanter).

### Steg

1. **Definiera tillåtna värden** som konstanter. `Handling` har redan
   `TYP_LABELS` (verifiera i `models.py`). `User` — lägg till:
   ```python
   class User(db.Model, UserMixin):
       ROLE_LABELS = {
           "admin": "Administratör",
           "registrator": "Registrator",
           "handlaggare": "Handläggare",
           "arkivarie": "Arkivarie",
           "observator": "Observatör",
       }
       TILLATNA_ROLLER = tuple(ROLE_LABELS.keys())
   ```
   (Om `ROLE_LABELS` redan finns — verifiera; annars lägg till.)
2. **`admin.py:ny_anvandare` (rad 47):**
   ```python
   role = request.form.get("role", "")
   if role not in User.TILLATNA_ROLLER:
       flash(f"Ogiltig roll. Tillåtna: {', '.join(User.TILLATNA_ROLLER)}.", "danger")
       return render_template("admin/ny_anvandare.html")
   user = User(..., role=role, ...)
   ```
3. **`admin.py:redigera_anvandare` (rad 117):** samma validering.
4. **`handlingar.py:ny` (rad 126):**
   ```python
   typ = request.form.get("typ", "").strip()
   if typ not in Handling.TYP_LABELS:  # antag att TYP_LABELS existerar
       flash(f"Ogiltig handlingstyp.", "danger")
       return render_template("handlingar/ny.html", arende=arende, kategorier=kategorier)
   ```
   (Om `Handling.TYP_LABELS` inte finns — lägg till i `models.py`:
   `TYP_LABELS = {"inkommande": "Inkommande", "utgaende": "Utgående",
   "upprattad": "Upprättad"}`.)
5. **`handlingar.py:redigera` (rad 252):** samma validering av `typ`.

### Regressionsrisk
- Befintliga tester som sätter giltig roll/typ påverkas inte.
- Om något test sätter ogiltig roll och förväntar sig att den sparas —
  uppdatera testet.

### Teststrategi
- `test_ny_anvandare_ogiltig_roll_visar_fel` — POST med `role=superuser`
  → ingen användare skapad, flash.
- `test_redigera_anvandare_ogiltig_roll_visar_fel`.
- `test_ny_handling_ogiltig_typ_visar_fel` (kan kombineras med uppgift 12).
- `test_redigera_handling_ogiltig_typ_visar_fel`.

---

## Uppgift 14 — N+1-frågor i listor/export

**Problem:** Routes itererar ärenden och lazy-loadar relationer per rad.
- `app/routes/arenden.py:exportera` (rad 168–175): `a.handlaggare.full_name`
  per rad → N+1.
- `app/auth.py:dashboard`: `senaste` (rad 156–161) och `stats` —
  template-rendering lazy-loadar handläggare/skapare per rad.
- `app/routes/arkiv.py:exportera` (rad 49–72): `h.versioner.all()` och
  `v.skapare.full_name` per version → N+1.
- `app/routes/admin.py:api_nycklar` (rad 230–235): `APIKey.anvandare`
  joinad (OK), men template kan lazy-loada.

**Berörda filer:** `arenden.py`, `auth.py`, `arkiv.py`, ev. templates.

### Steg

1. **`arenden.py:exportera` (rad 153):** lägg `joinedload`:
   ```python
   from sqlalchemy.orm import joinedload
   query = (
       Arende.query.filter_by(deleted=False)
       .options(joinedload(Arende.handlaggare), joinedload(Arende.skapare))
   )
   ```
2. **`auth.py:dashboard`:** `senaste`-query (rad 156):
   ```python
   senaste = (
       Arende.query.filter_by(deleted=False)
       .options(joinedload(Arende.handlaggare), joinedload(Arende.skapare))
       .order_by(Arende.skapad_datum.desc())
       .limit(10)
       .all()
   )
   ```
   `mina_arenden` (rad 146): samma.
3. **`arkiv.py:exportera` (rad 46):**
   ```python
   arende = (
       Arende.query.options(
           joinedload(Arende.handlingar).joinedload(Handling.versioner).joinedload(DocumentVersion.skapare),
           joinedload(Arende.skapare),
           joinedload(Arende.handlaggare),
       )
       .get_or_404(arende_id)
   )
   ```
   (Kedjade joinedload kan bli komplexa; verifiera SQL. Alternativt
   eagerload via `subqueryload` för `versioner`.)
4. **Verifiera med SQLAlchemy echo** i tester: slå på
   `app.config["SQLALCHEMY_ECHO"] = True` i ett tillfälligt test och
   räkna queries för en listvyn.

### Regressionsrisk
- Låg. `joinedload` ändrar inte resultat, bara hur det laddas.
- Kedjade joinedload kan generera kartesisk produkt om relationer är
  multi-valued — använd `subqueryload` för `versioner`/`handlingar` om
  query blir stor.

### Teststrategi
- Befintliga tester ska passera oförändrade.
- Lägg till `test_exportera_arenden_ger_inte_n_plus_1` — räkna queries
  före/efter (med `SQLALCHEMY_RECORD_QUERIES`) för N=1 vs N=10 ärenden;
  antal queries ska vara konstant.

---

## Uppgift 15 — Stora blobbar i minne (DB + send_file)

**Problem:** `DocumentVersion.fildata = db.LargeBinary` (models.py:167)
lagrar hela filen i DB. `handlingar.py:ladda_ner` (rad 238) och
`api.py:ladda_ner_fil` (rad 631) materialiserar hela blobben i
`io.BytesIO(version.fildata)`. `handlingar.py:_validera_fil` (rad 69)
läser hela filen i minne (`fil.read()`) innan storlekscheck.

**Berörd fil:** `app/routes/handlingar.py`, `app/routes/api.py`,
`app/models.py` (arkitektur), ny migration om filsystem-lagring införs.

### Steg — två spår

**Spår A (lågrisk, rekommenderas i denna plan):** minimera minnesanvändning
utan att ändra lagringsmodell.
1. **`_validera_fil` (rad 69):** kontrollera storlek **innan** hela filen
   läses:
   ```python
   fil.stream.seek(0, 2)  # seek to end
   storlek = fil.stream.tell()
   fil.stream.seek(0)
   if storlek > _max_fil_storlek_bytes():
       raise ValueError(f"Filen är för stor...")
   fildata = fil.read()
   ```
   (Werkzeug `FileStorage.stream` är en `SpooledTemporaryFile`; seek fungerar.)
2. **`ladda_ner`/`ladda_ner_fil`:** istället för `io.BytesIO(version.fildata)`,
   använd streamning:
   ```python
   from flask import Response
   def _stream_blob(data, mime):
       def generate():
           chunk = 64 * 1024
           for i in range(0, len(data), chunk):
               yield data[i:i+chunk]
       return Response(generate(), mimetype=mime)
   ```
   Detta materialiserar fortfarande hela blobben från DB (ofrånkomligt med
   `LargeBinary`), men streamar ut den i chunks istället för att bygga en
   stor BytesIO. Marginell vinning; verklig lösning kräver Spår B.
3. **Loggning före send_file** (från uppgift 4) ska ske innan streamen
   startas.

**Spår B (högrisk, egen plan rekommenderas):** migrera till filystemslagring.
- Ny kolumn `DocumentVersion.fil_sokvag` (nullable); nytt ansvar: skriva
  filer till `/var/dfs2/files/<hash>` eller objektlagring (S3).
- Migration som flyttar existerande blobbar till filsystem.
- Komplexitet: arkivlagens krav på oföränderlighet, backup, integritet.
- **Rekommendation:** Bryt ut Spår B till en separat plan; implementera
  endast Spår A här.

### Regressionsrisk
- Spår A: låg. `fil.stream.seek` kan bete sig olika för test-klientens
  `FileStorage` — verifiera att `tests/test_routes.py` filuppladdningstester
  passerar.
- Spår B: hög — rör lagringsarkitektur och arkivlagsefterlevnad.

### Teststrategi
- `test_validera_fil_storlekscheck_fore_lasning` — mocka `fil.read` och
  verifiera att det inte anropas om storlek > max.
- Befintliga filuppladdnings- och nedladdningstester ska passera.

---

## Uppgift 16 — `Installning.get` använder legacy `Query.get`

**Problem:** `app/models.py:203` — `cls.query.get(key)` använder
`Query.get` som är deprecated i SQLAlchemy 2.0.

**Berörd fil:** `app/models.py` (rad 201–204), sök efter andra `.query.get`
-användningar i hela kodbasen.

### Steg

1. **Sök efter alla `.query.get(`** i `app/`:
   ```bash
   rg "\.query\.get\(" app/
   ```
   (Förväntade träffar: `models.py:203`, `admin.py:149` `Installning.query.get`,
   `admin.py:250` `User.query.get`, routes som gör `<Model>.query.get_or_404`
   — `get_or_404` är OK, bara `.get()` utan `_or_404` är legacy.)
2. **Ersätt `.query.get(id)` med `db.session.get(Model, id)`:**
   ```python
   # Före:
   row = cls.query.get(key)
   # Efter:
   row = db.session.get(cls, key)
   ```
3. **Specifika ändringar:**
   - `models.py:203` `Installning.get`: `db.session.get(cls, key)`.
   - `admin.py:149` `Installning.query.get("standardprefix")` →
     `db.session.get(Installning, "standardprefix")`.
   - `admin.py:250` `User.query.get(user_id)` → `db.session.get(User, user_id)`.
   - `api.py` och andra: granska och ersätt.
4. **`get_or_404` är OK** — Flask-SQLAlchemy stöder det; lämna.
5. **Kör hela testsviten** — dessa ändringar ska vara transparenta.

### Regressionsrisk
- Mycket låg. `db.session.get` returnerar samma objekt som `Query.get`.

### Teststrategi
- Inga nya tester; befintliga tester verifierar oförändrat beteende.

---

## Uppgift 17 — Hårdkodad `admin@example.com` i seed

**Problem:** `seed.py:41` — `email="admin@example.com"` hårdkodat. Bör
vara konfigurerbar via env.

**Berörd fil:** `seed.py` (rad 38–44).

### Steg

1. **Läs email från env** med fallback:
   ```python
   admin_email = (
       os.environ.get("ADMIN_EMAIL")
       or _las_hemlighet_from_file("admin_email")
       or "admin@example.com"
   )
   ```
   (Enklare: bara `os.environ.get("ADMIN_EMAIL", "admin@example.com")`.)
2. **Dokumentera** `ADMIN_EMAIL` i `.env.example` och `CLAUDE.md`.
3. Logga vid seed vilken email som sattes (utan att läcka om den är känslig).

### Regressionsrisk
- Ingen. Default är oförändrad om `ADMIN_EMAIL` inte sätts.

### Teststrategi
- `test_seed_anvander_admin_email_env` — monkeypatcha `ADMIN_EMAIL`,
  kör seed (i test-app-kontext), verifiera att admin-user får den emailen.
- (Seed-tester kan saknas; lägg till ett enkelt test om möjligt.)

---

## Uppgift 18 — f-string i SQL för statement_timeout

**Problem:** `app/__init__.py:93` —
`cursor.execute(f"SET statement_timeout = {timeout_ms}")`. Värdet är en int
(säkert mot injection), men f-string i SQL är ett anti-mönster som
flaggas vid granskning.

**Berörd fil:** `app/__init__.py` (rad 90–94).

### Steg

1. **Använd parameteriserad SQL** (PostgreSQL psycopg2 stöder det via
   `%s`-syntax, inte SQLAlchemy `:name`-syntax direkt på dbapi-conn):
   ```python
   @event.listens_for(db.engine, "connect")
   def _set_timeout(dbapi_conn, connection_record):
       cursor = dbapi_conn.cursor()
       cursor.execute("SET statement_timeout = %s", (timeout_ms,))
       cursor.close()
   ```
   (psycopg2 accepterar `%s`-parametrisering för `SET`. Verifiera att
   psycopg3 om används har samma syntax.)
2. **Verifiera** att inget test går sönder (testerna skippar denna hook
   eftersom `db.engine.dialect.name != "postgresql"` i SQLite, se rad 87).

### Regressionsrisk
- Ingen i tester (SQLite-skip). I prod: parameterisering av `SET` är
  standard; inget beteendeändras.

### Teststrategi
- Inga nya tester. Manuell verifiering i prod-DB att `statement_timeout`
  sätts korrekt efter deploy (`SHOW statement_timeout;`).

---

## Validering

Kör hela testsviten efter varje uppgift:

```bash
source .venv/bin/activate
pytest tests/ -v
```

Efter alla uppgifter:
```bash
pytest tests/ -v --tb=short
```
Förväntat: 0 misslyckade. Uppgift 15 Spår B och eventuella nya tester
noteras som kvarvarande om de bryts ut.

---

## Kommande planer (ej i denna plan)

- **19–27 (testluckor):** flera tester i denna plan (12, 13, 14) stänger
  luckor; notera överlapp. Specifikt: uppgift 19–22 täcks redan av plan
  1–5 och 6–10; uppgift 23 (`exportera` XLSX) berörs av uppgift 14
  (N+1-fix); uppgift 24 (`byt_losenord`) är otestat — lägg till.
- **Spår B (filystemslagring)** från uppgift 15: bryt ut till egen plan.
- **Per-API-nyckel rate limits** (från uppgift 9): framtida hardening.
- **AuditLog serialisering** (från uppgift 10): undvika kedjeförgrening.
