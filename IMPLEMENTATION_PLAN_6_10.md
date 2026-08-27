# Implementationsplan – DFS2 Sekretess & Korrekthet (Uppgift 6–10)

Denna plan täcker de medelprioriterade uppgifterna från kodbasgranskningen.
Förutsätter att uppgift 1–5 (se `IMPLEMENTATION_PLAN.md`) är klara, särskilt
hjälpfunktionen `Arende.sekretess_filter(query, user)` och `app/services.py`.

## Sekvensordning

- **Parallella (oberoende):** Uppgift 7 (config), 9 (rate limits).
- **Sekventiella:** Uppgift 6 bör göras efter uppgift 1 (återanvänder
  `Arende.sekretess_filter`). Uppgift 8 (logg-diff) är oberoende men rör
  samma routes som uppgift 3 — gör efter 3. Uppgift 10 (hashkedja) rör
  `log_action`/`AuditLog`-modell och bör göras sist i denna grupp eftersom
  den ändrar `log_action` som alla andra uppgifter använder.

---

## Uppgift 6 — Sök läcker sekretesshandlingar via avsandare/beskrivning

**Problem:** `app/routes/sok.py` har två brister:
1. Rad 33–35 applicerar sekretessfilter på `Arende` **endast för observatör**.
   Handläggare och arkivarie får se alla sekretessbelagda ärenden i sökträffar
   (bortsett från enskild visning som checkar ägandeskap).
2. Rad 66–86 (`avsandare`/`beskrivning`-filter) bygger en subquery på
   `Handling` utan att utesluta `Handling.sekretess == True` eller
   handlingar i sekretessbelagda ärenden. En sökträff avslöjar `arende_id`
   för sekretesshandlingar även för observatör (subqueryn returnerar
   ärende_id, sedan filtreras på Arende.sekretess — men för admin/
   registrator finns inget Arende-sekretessfilter alls, så sekretess-
   handlingar i öppna ärenden exponeras).

**Berörd fil:** `app/routes/sok.py`

### Steg

1. **Ersätt observator-only-filteret (rad 34–35)** med ett anrop till
   `Arende.sekretess_filter` från uppgift 1:
   ```python
   query = Arende.query.filter_by(deleted=False)
   query = Arende.sekretess_filter(query, current_user)
   ```
   Detta ger: observatör → bara `sekretess == False`; handläggare →
   `sekretess == False OR handlaggare_id == user.id`; arkivarie →
   `sekretess == False OR status == 'arkiverat'`; admin/registrator → allt.

2. **Sekretessfilter på handling-subqueries (rad 66–86 och 96–104).**
   Inför en hjälpfunktion i `sok.py` som bygger en handling-subquery med
   sekretessfilter baserat på roll:
   ```python
   def _handling_sekretess_filter(q):
       """Begränsa en Handling-query så att sekretesshandlingar bara
       inkluderas om current_user har rätt att se dem."""
       if current_user.role in ("admin", "registrator"):
           return q  # får se alla
       if current_user.role == "handlaggare":
           # Bara egna ärendens sekretesshandlingar
           from sqlalchemy import or_
           return q.filter(
               or_(
                   Handling.sekretess == False,
                   Handling.arende.has(Arende.handlaggare_id == current_user.id),
               )
           ).filter(
               or_(
                   Handling.arende.has(Arende.sekretess == False),
                   Handling.arende.has(Arende.handlaggare_id == current_user.id),
               )
           )
       if current_user.role == "arkivarie":
           from sqlalchemy import or_
           return q.filter(
               or_(
                   Handling.sekretess == False,
                   Handling.arende.has(Arende.status == "arkiverat"),
               )
           ).filter(
               or_(
                   Handling.arende.has(Arende.sekretess == False),
                   Handling.arende.has(Arende.status == "arkiverat"),
               )
           )
       # observator och övriga: bara icke-sekretess i icke-sekretess-ärenden
       return q.filter(
           Handling.sekretess == False,
           Handling.arende.has(Arende.sekretess == False),
       )
   ```
   Applicera på subqueriesna för `avsandare`, `beskrivning` och
   `typ_handling` (kategori). Exempel för avsandare:
   ```python
   if q.get("avsandare"):
       sub = Handling.query.filter(
           Handling.avsandare.ilike(f"%{_trunkera(q['avsandare'])}%"),
           Handling.deleted == False,
       )
       sub = _handling_sekretess_filter(sub)
       arende_ids = sub.with_entities(Handling.arende_id).distinct()
       query = query.filter(Arende.id.in_(arende_ids))
   ```
3. Verifiera att `Handling.arende.has(...)` genererar korrekt SQL på både
   PostgreSQL och SQLite (subquery-exists). Om prestanda är ett problem,
   använd en join mot `Arende` i stället:
   ```python
   sub = sub.join(Arende).filter(<samma sekretessvillkor på Arende>)
   ```

### Regressionsrisk
- Befintliga söktester (`tests/test_routes.py` runt `TestObservator` och
  sök-klassen) förväntar sig viss synlighet — verifiera per roll.
- Admin/registrator-behåller full synlighet, så inga admin-tester bör gå
  sönder.

### Teststrategi (`tests/test_routes.py`)
- `test_sok_observator_hittar_inte_sekretess_handling_via_beskrivning` —
  skapa öppet ärende med en sekretesshandling vars `beskrivning` är unik;
  observatör söker på den beskrivningen → 0 träffar.
- `test_sok_handlaggare_hittar_egen_sekretess_handling_men_inte_andras` —
  handläggare A äger ärende med sekretesshandling; handläggare B söker →
  0 träffar; A söker → 1 träff.
- `test_sok_admin_hittar_alla` — admin hittar både sekretess- och
  icke-sekretesshandlingar.
- `test_sok_arkivarie_hittar_sekretess_bari_arkiverade` — sekretesshandling
  i `status=arkiverat`-ärende → arkivarie hittar; samma handling i
  `status=pagaende`-ärende → arkivarie hittar inte.

---

## Uppgift 7 — `SECRET_KEY`-fallback maskerar produktionsfel

**Problem:** `config.py:40` — `SECRET_KEY = _las_hemlighet("SECRET_KEY", "secret_key") or secrets.token_hex(32)`. Om `SECRET_KEY` saknas i produktion genereras en ny slumpmässig ny vid varje start: sessioner ogiltiggörs vid omstart och felet maskeras.

**Berörd fil:** `config.py`, ev. `tests/conftest.py`.

### Steg

1. **Inför en miljöindikator** `DFS2_ENV` med värden `production`
   (standard), `development`, `test`:
   ```python
   DFS2_ENV = os.environ.get("DFS2_ENV", "production").lower()
   ```
2. **Ändra SECRET_KEY-raderna:**
   ```python
   _secret = _las_hemlighet("SECRET_KEY", "secret_key")
   if _secret:
       SECRET_KEY = _secret
   elif DFS2_ENV == "production":
       raise RuntimeError(
           "SECRET_KEY saknas i produktion. Sätt miljövariabeln "
           "SECRET_KEY eller Docker Secret 'secret_key'."
       )
   else:
       # development/test — tillåt genererad nyckel men varna
       SECRET_KEY = secrets.token_hex(32)
   ```
3. **Uppdatera `tests/conftest.py`** så att `DFS2_ENV=test` sätts innan
   `create_app()` importeras/körs:
   ```python
   os.environ["DATABASE_URL"] = "sqlite:///:memory:"
   os.environ["DFS2_ENV"] = "test"
   ```
   (`conftest.py` sätter redan `SECRET_KEY="test-secret"` via
   `app.config.update` efter `create_app()`, men `Config` laddas först —
   därför måste `DFS2_ENV=test` sättas innan.)
4. **Dokumentera** i `.env.example` och `CLAUDE.md` att `DFS2_ENV` bör
   sättas till `development` lokalt och utelämnas (eller `production`) i
   prod. Docker Compose bör sätta `DFS2_ENV=production` explicit.

### Regressionsrisk
- Om `DFS2_ENV` inte sätts i en existerande dev-miljö kommer appen krascha
  vid uppstart (RuntimeError). Detta är avsett (fail-loud) men kan överraska.
  Kommunicera i release notes.
- Tester: `conftest.py`-ändringen är kritisk — utan den går hela testsviten
  sönder.

### Teststrategi (`tests/test_config.py` — ny fil om ej finns)
- `test_config_kraschar_utan_secret_key_i_production` — monkeypatcha bort
  `SECRET_KEY`/secret-fil, sätt `DFS2_ENV=production`, förvänta
  `RuntimeError` vid import av `Config`.
- `test_config_tillater_fallback_i_test` — `DFS2_ENV=test`, ingen
  `SECRET_KEY` → ingen krasch, `SECRET_KEY` är 64 hex-tecken.

---

## Uppgift 8 — Sekretessflaggändring loggas utan diff

**Problem:** När `sekretess` ändras på en Handling eller ett Arende loggas
inte gammalt/nytt värde. För full spårbarhet (OSL) ska av-/tillklassning
loggas med `fran`/`till`.

**Berörda filer:**
- `app/routes/handlingar.py` — `redigera` (rad 245–283), särskilt rad 257
  (`handling.sekretess = "sekretess" in request.form`) och loggen 265–271.
- `app/routes/arenden.py` — `redigera` (rad 92–118), särskilt rad 99
  (`arende.sekretess = "sekretess" in request.form`) och loggen 104–110.
- `app/routes/api.py` — `redigera_handling` (rad 518–555, sekretess 540–541,
  logg 547–553) och `redigera_arende` (rad 306–335, sekretess 320–321,
  logg 327–333).

### Steg

1. **`handlingar.py` `redigera`:** hämta gammalt värde före tilldelning:
   ```python
   gammal_sekretess = handling.sekretess
   # ... befintlig kod som sätter handling.sekretess ...
   ny_sekretess = handling.sekretess
   log_details = {"arende": handling.arende.diarienummer}
   if ny_sekretess != gammal_sekretess:
       log_details["sekretess_fran"] = gammal_sekretess
       log_details["sekretess_till"] = ny_sekretess
       log_details["sekretess_andrad"] = True
   log_action(current_user.id, "redigera_handling", "Handling",
              handling.id, log_details)
   ```
2. **`arenden.py` `redigera`:** motsvarande för `arende.sekretess`:
   ```python
   gammal_sekretess = arende.sekretess
   # ... arende.sekretess = "sekretess" in request.form ...
   log_details = {"diarienummer": arende.diarienummer}
   if arende.sekretess != gammal_sekretess:
       log_details["sekretess_fran"] = gammal_sekretess
       log_details["sekretess_till"] = arende.sekretess
       log_details["sekretess_andrad"] = True
   log_action(...)
   ```
3. **`api.py` `redigera_handling` och `redigera_arende`:** samma mönster.
   För API:t sätts sekretess endast om `"sekretess" in body` — hämta
   `gammal_sekretess` före, jämför efter.
4. **Överväg separat action-typ** för sekretessändring: antingen behåll
   `redigera_handling`/`redigera_arende` med `sekretess_andrad: True` i
   details (enklare, bakåtkompatibelt) ELLER lägg till ny action
   `andra_sekretess`. **Rekommendation:** behåll befintlig action med
   `sekretess_andrad`-flagga — gör det enkelt att filtrera i admin-vyn utan
   schemaändring.

### Regressionsrisk
- Befintliga tester som assertar `logg.details == {...}` exakt kommer gå
  sönder om details utökas. Sök efter sådana assertions (t.ex.
  `tests/test_routes.py:2028`-området) och uppdatera till subset-check
  (`details["arende"] == ...`) eller inkludera nya fält.

### Teststrategi (`tests/test_routes.py`)
- `test_sekretessandring_loggas_med_diff_handling` — skapa handling med
  `sekretess=False`, redigera till `sekretess=True`, verifiera
  `AuditLog.details` innehåller `sekretess_fran=False`, `sekretess_till=True`.
- `test_sekretessandring_loggas_med_diff_arende` — motsvarande för ärende.
- `test_sekretess_oforandrad_loggas_utan_diff` — redigera annan field,
  verifiera att `sekretess_andrad` inte finns i details.

---

## Uppgift 9 — Saknade rate limits på API

**Problem:** `app/routes/api.py` är CSRF-undtagen (se `app/__init__.py:57`)
och använder Bearer-auth, men har inga rate limits. En läckt/bruteforce-
bar API-nyckel kan göra obegränsade anrop. `app/auth.py:62` har redan
mönstret `@limiter.limit("20 per minute")` för login.

**Berörd fil:** `app/routes/api.py`, ev. `app/__init__.py`.

### Steg

1. **Importera limiter** i `api.py`:
   ```python
   from app import limiter
   ```
2. **Applicera rate limits per endpoint-kategori.** Läs-endepts (GET) får
   högre gräns, skrivande (POST/PUT) lägre:
   - GET `lista_arenden`, `hamta_arende`, `hamta_handling`, `hamta_brukare`,
     `ladda_ner_fil`: `@limiter.limit("120 per minute")`
   - POST/PUT `skapa_arende`, `redigera_arende`, `byt_status`,
     `skapa_handling`, `redigera_handling`, `ladda_upp_version`,
     `hamta_brukare`: `@limiter.limit("30 per minute")`
   Placera `@limiter.limit(...)` **ovanför** `@blp.response`/`@blp.arguments`
   så att det applies på view-funktionen.
3. **Per-API-nyckel-limit (önskemål):** för att begränsa per nyckel i
   stället för per IP, använd en custom key_func:
   ```python
   def _api_key_func():
       from flask import g
       return f"apikey:{g.api_user.id}" if g.get("api_user") else get_remote_address()

   @limiter.limit("120 per minute", key_func=_api_key_func)
   ```
   Men `_check_auth` körs inuti view-funktionen, så `g.api_user` inte satt
   när limiter utvärderas. flask-limiter utvärderar dekoratorn **innan**
   view-kroppen. Lösning: använd en `before_request`-hook som anropar
   `_check_auth` för `/api/v1/*` och sätter `g.api_user`, sedan kan
   key_func använda `g.api_user`. **Förenklad rekommendation för denna
   plan:** använd IP-baserad limit (default `get_remote_address`) — det
   skyddar mot brute-force och DoS. Per-nyckel-limit kan läggas till i
   en senare iteration.
4. **Verifiera att testerna passerar** — `conftest.py` sätter
   `_limiter.enabled = False` (rad 17) så rate limits är av i tester.
   Inga teständringar krävs.

### Regressionsrisk
- Om `_limiter.enabled` av någon anledning är True i tester går de sönder
  vid många anrop.Verifiera att conftest sätter det False före
  `create_app()` (gör den, rad 17).
- Echt produktionsrollout: gränserna kan vara för låga för legitima
  batch-skript. Justera via env var om möjligt:
  ```python
  API_RATE_LIMIT_READ = os.environ.get("API_RATE_LIMIT_READ", "120 per minute")
  API_RATE_LIMIT_WRITE = os.environ.get("API_RATE_LIMIT_WRITE", "30 per minute")
  ```
  i `config.py` och referera i dekoratorer.

### Teststrategi (`tests/test_routes.py`)
- `test_api_rate_limit_aktiverad_i_prod` — separat test-app med
  `limiter.enabled=True`, gör 31 POST → förvänta 429 på den 31:a.
  (Skippa om svårt att isolera; notera som manuell verifiering.)

---

## Uppgift 10 — AuditLog saknar oföränderlighetsskydd (hashkedja)

**Problem:** `AuditLog` (se `app/models.py:178–192`) är en vanlig tabell.
Direkta DB-ändringar/raderingar kan inte upptäckas. Arkivlagen kräver
granskningsloggens integritet. Inför en hashkedja där varje post refererar
föregående posts hash.

**Berörda filer:**
- `app/models.py` — `AuditLog`-klassen (178–192) och `log_action` (263–274)
- Ny migration: `migrations/versions/0004_audit_log_hash.py`
- `app/routes/admin.py` — granskningslogg-vy (ev. lägg till
  kedjeintegritets-indikator)

### Steg

1. **Lägg till kolumner** i `AuditLog`-modellen:
   ```python
   prev_hash = db.Column(db.String(64), nullable=True)  # SHA-256 hex
   entry_hash = db.Column(db.String(64), nullable=True)  # SHA-256 hex
   ```
   `prev_hash` är NULL för första posten; `entry_hash` = `sha256(prev_hash || canonical_payload)`.
2. **Skapa migration `0004_audit_log_hash.py`:**
   ```python
   """Add hash columns to audit_log

   Revision ID: 0004
   Revises: 0003
   Create Date: 2026-08-26
   """
   import sqlalchemy as sa
   from alembic import op

   revision = "0004"
   down_revision = "0003"
   branch_labels = None
   depends_on = None

   def upgrade():
       op.add_column("audit_log", sa.Column("prev_hash", sa.String(64), nullable=True))
       op.add_column("audit_log", sa.Column("entry_hash", sa.String(64), nullable=True))
       # Backfill: sätt entry_hash="" för existerande rader (bakåtkompatibelt;
       # verify_chain() flaggar dem som "obestyrkta" men inte som manipulerade).
       op.execute("UPDATE audit_log SET entry_hash = '' WHERE entry_hash IS NULL")

   def downgrade():
       op.drop_column("audit_log", "entry_hash")
       op.drop_column("audit_log", "prev_hash")
   ```
3. **Ändra `log_action`** så att den beräknar hash:
   ```python
   import hashlib, json

   def _compute_entry_hash(prev_hash, payload):
       """SHA-256 över (prev_hash + canonical JSON av payload)."""
       canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
       h = hashlib.sha256()
       h.update((prev_hash or "").encode("utf-8"))
       h.update(b"|")
       h.update(canon.encode("utf-8"))
       return h.hexdigest()

   def _audit_payload(entry):
       """Det oföränderliga innehållet som ska hashas."""
       return {
           "user_id": entry.user_id,
           "action": entry.action,
           "target_type": entry.target_type,
           "target_id": entry.target_id,
           "details": entry.details,
           "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
           "ip_address": entry.ip_address,
       }

   def log_action(user_id, action, target_type=None, target_id=None, details=None):
       from flask import request
       entry = AuditLog(
           user_id=user_id,
           action=action,
           target_type=target_type,
           target_id=target_id,
           details=details,
           ip_address=request.remote_addr if request else None,
       )
       # timestamp sätts av default; flush för att få den
       db.session.add(entry)
       db.session.flush()
       # Hämta föregående posts hash (senaste id < detta id)
       prev = (
           AuditLog.query.filter(AuditLog.id < entry.id)
           .order_by(AuditLog.id.desc())
           .first()
       )
       prev_hash = prev.entry_hash if prev else None
       entry.prev_hash = prev_hash
       entry.entry_hash = _compute_entry_hash(prev_hash, _audit_payload(entry))
       # Ingen extra flush krävs; commit sker av anroparen.
   ```
4. **Lägg till `verify_chain`-klassmetod:**
   ```python
   @classmethod
   def verify_chain(cls, start_id=1):
       """Verifiera hashkedjan. Returnerar lista med id:n för brutna/ändrade poster."""
       broken = []
       prev_hash = None
       for entry in cls.query.order_by(cls.id.asc()).filter(cls.id >= start_id).all():
           if entry.prev_hash != prev_hash:
               broken.append((entry.id, "prev_hash_mismatch"))
           elif entry.entry_hash != _compute_entry_hash(prev_hash, _audit_payload(entry)):
               broken.append((entry.id, "hash_mismatch"))
           prev_hash = entry.entry_hash
       return broken
   ```
   (Lägg `_compute_entry_hash`/`_audit_payload` som modul-funktioner så
   `verify_chain` kan nå dem.)
5. **Admin-vy** (`app/routes/admin.py` granskningslogg-route): lägg till en
   banner om `AuditLog.verify_chain()` returnerar poster. Undvik att köra
   verifieringen på varje sidvisning (kan bli tung) — kör t.ex. endast om
   `?verify=1` sätts, eller cacha resultatet.
6. **Skydd mot radering:** hashkedjan detekterar radering (kedjan bryts
   vid nästa post). För att förhindra radering på DB-nivå krävs
   PostgreSQL `REVOKE DELETE` eller en trigger — notera som framtida
   hardening, implementera ej i denna plan.

### Känd begränsning — konkurrens
Två samtidiga `log_action`-anrop kan läsa samma `prev.entry_hash` och
producera en "förgrening" där kedjan bryts vid nästa post. Detta är
sällsynt (loggning är snabb) men möjligt med `--workers 2`. Hantering:
- Acceptera som känd begränsning i denna plan; `verify_chain` flaggar
  förgreningar som `prev_hash_mismatch`.
- Framtida lösning: serialisera loggning via en dedikerad sekvens-rad
  ( likt `Nummerserie`) eller `SELECT ... FOR UPDATE` på senaste post.
  Notera i "Kommande planer".

### Regressionsrisk
- **Alla tester som skapar AuditLog-poster påverkas** eftersom `log_action`
  ändras. Men eftersom kolumnerna är nullable och `log_action` alltid
  sätter dem, bör befintliga assertions (`AuditLog.query.first()` etc.)
  fortsätta fungera. Verifiera att `db.session.flush()` inuti `log_action`
  inte krockar med anroparens transaktionshantering.
- Test-DB använder `create_all()` (inte migrations) så kolumnerna måste
  finnas i modellen — steg 1 täcker det.
- Befintliga poster i prod-DB backfillas till `entry_hash=''` av
  migrationen; `verify_chain` flaggar dem inte som manipulerade men de
  utgör kedjans "orot" — första nya posten efter migration får
  `prev_hash=''` och kedjan börjar där.

### Teststrategi (`tests/test_models.py`)
- `test_audit_log_hash_kedja_hel` — skapa 3 poster, verifiera att
  `verify_chain()` returnerar `[]`.
- `test_audit_log_hash_detekterar_andring` — skapa post, manipulera
  `details` direkt via `db.session.execute(update(...))`, verifiera att
  `verify_chain()` flaggar posten.
- `test_audit_log_hash_detekterar_radering` — skapa 3 poster, radera
  mittersta, verifiera att `verify_chain()` flaggar tredje posten
  (`prev_hash_mismatch`).
- `test_log_action_satter_prev_hash_for_forsta_posten_null` — första
  posten i tom tabell har `prev_hash=None`.

---

## Validering

Kör hela testsviten efter varje uppgift:

```bash
source .venv/bin/activate
pytest tests/ -v
```

Specifika testfall som ska passera:

- **Uppgift 6:** `test_sok_observator_hittar_inte_sekretess_handling_via_beskrivning`
  + handläggare/arkivarie-motsvarigheter.
- **Uppgift 7:** `test_config_kraschar_utan_secret_key_i_production` +
  `test_config_tillater_fallback_i_test`.
- **Uppgift 8:** `test_sekretessandring_loggas_med_diff_handling` +
  `_arende` + `test_sekretess_oforandrad_loggas_utan_diff`.
- **Uppgift 9:** bekräfta att `limiter.enabled=False` i conftest fortfarande
  gäller och att alla befintliga API-tester passerar.
- **Uppgift 10:** `test_audit_log_hash_kedja_hel` +
  `test_audit_log_hash_detekterar_andring` +
  `test_audit_log_hash_detekterar_radering`.

Efter alla fem:
```bash
pytest tests/ -v --tb=short
```
Förväntat: 0 misslyckade.

---

## Kommande planer (ej i denna plan)

- **11–18 (LÅG):** duplicerad söklogik (använd `_handling_sekretess_filter`
  + `Arende.sekretess_filter` som grund för en gemensam sök-service i
  `app/services.py`), ohanterade ValueError/KeyError, validering av
  `role`/`typ`, N+1-frågor, stora blobbar, legacy `Query.get`, hårdkodad
  e-post, f-string i SQL.
- **19–27 (testluckor):** flera tester i denna plan (6, 7, 8, 10) stänger
  motsvarande luckor; notera vilka som redan täcks så de inte dupliceras.
- **Framtida hardening av AuditLog:** serialisering av `log_action` för att
  undvika kedjeförgrening vid konkurrens; PostgreSQL `REVOKE DELETE` på
  `audit_log`; per-API-nyckel rate limits (se uppgift 9).
