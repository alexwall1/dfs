# Implementationsplan – DFS2 Säkerhet & Regelverk (Uppgift 1–5)

Denna plan täcker de fem högprioriterade uppgifterna från kodbasgranskningen.
Mål: eliminera OSL/Arkivlagen-brott och datakonsistensbuggar innan övriga
förbättringar (6–10, 11–18, 19–27) påbörjas.

## Sekvensordning

- **Parallella (oberoende):** Uppgift 1, 2, 4 kan göras parallellt.
- **Sekventiella:** Uppgift 3 bör göras före eller tillsammans med 5 (båda
  rör `byt_status`/hjälpfunktioner). Uppgift 5 bygger på hjälpfunktionerna
  från 3.

---

## Uppgift 1 — Sekretessfilter i listor för observatör (OSL-brott)

**Problem:** `lista`- och dashboard-listor exponerar `arende_mening` för
sekretessbelagda ärenden för observatörer (och handläggare som inte äger
ärendet). Enskild visning (`visa`) och sökning har filter, men inte listorna.

**Berörda filer:**
- `app/routes/arenden.py` — `lista` (rad 14–28)
- `app/auth.py` — `dashboard` (rad 132–232): `stats` (138–142), `senaste`
  (156–161), `mina_arenden` (144–154)

**Referensmönster:** `app/routes/api.py:232–242` (`lista_arenden`) har rätt
per-roll-filter:
```python
if user.role == "observator":
    query = query.filter(Arende.sekretess == False)
elif user.role == "handlaggare":
    query = query.filter(or_(Arende.sekretess == False, Arende.handlaggare_id == user.id))
elif user.role == "arkivarie":
    query = query.filter(or_(Arende.sekretess == False, Arende.status == "arkiverat"))
```

### Steg

1. **`app/routes/arenden.py` `lista`** — efter `query = Arende.query.filter_by(deleted=False)` (rad 18), inför rollfilter enligt referensmönstret ovan. Importera `or_` från `sqlalchemy`. `admin`/`registrator` behöver inget filter.
2. **`app/auth.py` `dashboard`:**
   - `stats` (rad 138–142): de tre räknarna summerar över alla ärenden oavsett sekretess. För observatör bör endast icke-sekretessbelagda räknas. Byt till en gemensam bas-query-funktion `_sekretess_filter(query, user)` (se nedan) och applicera på varje räkning för observatör/handlaggare/arkivarie.
   - `senaste` (rad 156–161): applicera samma filter.
   - `mina_arenden` (rad 144–154): handläggarens egna ärenden — handläggare får se sekretess på egna ärenden, så inget ytterligare filter krävs, men verifiera att listan inte läcker andras sekretessärenden (den gör det inte, eftersom det filtreras på `handlaggare_id == current_user.id`).
3. **Inför en gemensam hjälpfunktion** (lägg i `app/models.py` som klassmetod på `Arende`, alternativt i en ny `app/services.py`):
```python
@classmethod
def sekretess_filter(cls, query, user):
    """Applicera sekretessfilter på en Arende-query baserat på användarens roll."""
    from sqlalchemy import or_
    if user.role == "observator":
        return query.filter(cls.sekretess == False)  # noqa: E712
    if user.role == "handlaggare":
        return query.filter(or_(cls.sekretess == False, cls.handlaggare_id == user.id))
    if user.role == "arkivarie":
        return query.filter(or_(cls.sekretess == False, cls.status == "arkiverat"))
    return query  # admin, registrator
```
   Använd denna i `lista`, `senaste`, `stats` och (senare) i export/sök.

### Regressionsrisk
- Befintliga tester för `lista`/dashboard antar full synlighet — måste verifieras per roll.
- `exportera` (arenden.py:149) har inget sekretessfilter heller men hanteras i en senare plan (uppgift 6-gruppen); röra den **inte** här för att hålla diffen liten.

### Teststrategi (`tests/test_routes.py`)
Lägg till i befintlig `TestObservator`-klass:
- `test_observator_lista_exkluderar_sekretess_arenden` — skapa ett sekretess- och ett offentligt ärende, logga in som observatör, hämta `/arenden/`, assert att endast det offentliga visas.
- `test_observator_dashboard_senaste_exkluderar_sekretess` — samma men mot `/dashboard`, kontrollera att `senaste`-listan inte innehåller sekretessärendet.
- `test_observator_dashboard_stats_exkluderar_sekretess` — kontrollera att `stats["oppna"]` inte räknar sekretessärenden för observatör.
- `test_handlaggare_lista_ser_egen_sekretess_men_inte_andras` — handläggare A äger sekretessärende, handläggare B ska inte se det i `/arenden/`.

---

## Uppgift 2 — Race condition i `Nummerserie.next_number`

**Problem:** `app/models.py:219–228` gör read-then-increment utan radlås.
Med `gunicorn --workers 2` (se `entrypoint.sh`) kan två samtidiga POST
`/arenden/ny` läsa samma `current_number` och producera dubblett.

**Berörd fil:** `app/models.py` (`Nummerserie.next_number`, rad 219–228).

### Steg

Ersätt metodkroppen med en atomisk UPDATE … RETURNING, med fallback för
saknad rad (första numret för året). SQLAlchemy-syntax:

```python
@classmethod
def next_number(cls, prefix):
    from sqlalchemy import text
    now = datetime.now(timezone.utc)

    # Atomisk inkrement: UPDATE ... SET current_number = current_number + 1
    # ... RETURNING current_number
    result = db.session.execute(
        text(
            "UPDATE nummerserier "
            "SET current_number = current_number + 1 "
            "WHERE prefix = :prefix AND year = :year "
            "RETURNING current_number"
        ),
        {"prefix": prefix, "year": now.year},
    )
    row = result.first()

    if row is None:
        # Första numret för året — skapa raden atomiskt.
        # Fånga UniqueConstraint("prefix","year") vid kapplöpning.
        from sqlalchemy.exc import IntegrityError
        serie = cls(prefix=prefix, year=now.year, current_number=1)
        db.session.add(serie)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            # En annan worker hann före — gör om inkrementet.
            return cls.next_number(prefix)
        nxt = 1
    else:
        nxt = row[0]

    return f"{prefix}-{now.year}-{nxt:04d}"
```

### Anmärkningar / SQLite-kompatibilitet
- SQLite (test) saknar `UPDATE ... RETURNING` på äldre drivrutiner.
  SQLAlchemy ≥ 1.4 + Python sqlite3 stöder RETURNING sedan Python 3.10+.
  Verifiera att testerna går igenom; om inte, implementera en fallback med
  `with_for_update()`:
  ```python
  serie = (
      cls.query.filter_by(prefix=prefix, year=now.year)
      .with_for_update()
      .first()
  )
  ```
  `with_for_update()` är en no-op på SQLite men ger korrekt beteende på
  PostgreSQL. Välj den variant som testerna godkänner.
- Bevara returvärdesformatet exakt: `"{PREFIX}-{ÅÅÅÅ}-{NNNN}"` med
  nollpaddding till 4 siffror.

### Regressionsrisk
- Befintligt test `test_next_number` (eller liknande i `test_models.py`)
  verifierar format — ska fortsätta passera.
- `seed.py` och admin-route för nummerserier kan förvänta sig att `current_number`
  uppdateras synkront — bekräfta att `flush`/`commit`-beteende oförändrat.

### Teststrategi
- `tests/test_models.py`: lägg till `test_next_number_ar_unik_vid_kapplöpning` —
  skapa en Nummerserie-rad, anropa `next_number` två gånger i samma session och
  verifiera att andra numret = första + 1.
- (Frivilligt) ett integrationstest med `threading` som anropar `next_number`
  100 gånger parallellt och verifierar 100 unika nummer. Kräver PostgreSQL för
  att vara meningsfullt — märk som `@pytest.mark.skipif(SQLite)`.

---

## Uppgift 3 — Mjuk borttagning respekteras i redigera/status/ny-version

**Problem:** Routes använder `get_or_404` utan att kontrollera `deleted`.
Borttagna ärenden/handlingar kan redigeras, få status ändrad eller nya
filversioner. API:et (`api.py`) har redan korrekta checkar — följ det
mönstret.

**Berörda filer:**
- `app/routes/arenden.py`: `redigera` (rad 95), `byt_status` (rad 124),
  `ta_bort` (rad 197)
- `app/routes/handlingar.py`: `ny` (rad 103), `ny_version` (rad 188),
  `redigera` (rad 248), `ta_bort` (rad 289), `ladda_ner` (rad 235 — verifiera
  handling+arende ej borttagna), `visa` (rad 176 — verifiera)
- `app/routes/api.py`: redan korrekt på `redigera_arende`/`byt_status`/
  `skapa_handling`/`ladda_upp_version`/`redigera_handling` — **ingen ändring
  krävs**, men dubbelkolla `hamta_handling` (510) och `ladda_ner_fil` (624).

### Steg

1. **Skapa hjälpfunktioner** i en ny modul `app/services.py` (eller som
   statiska metoder på modellerna i `app/models.py`). Föredra en separat
   `services.py` för testbarhet:
   ```python
   # app/services.py
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
   ```
2. **`arenden.py`:** byt ut `Arende.query.get_or_404(arende_id)` mot
   `hamta_aktivt_arende(arende_id)` i `redigera`, `byt_status`, `ta_bort`.
   Importera från `app.services`.
   - För `ta_bort`: kontrollera också att ärendet inte redan är borttaget
     (idempotent — `abort(404)` om redan borttaget, vilket `hamta_aktivt_arende`
     ger).
3. **`handlingar.py`:** byt ut `Handling.query.get_or_404(...)` och
   `Arende.query.get_or_404(...)`:
   - `ny`: `arende = hamta_aktivt_arende(arende_id)` (kan ej lägga handling på
     borttaget ärende).
   - `ny_version`: `handling = hamta_aktiv_handling(handling_id)`; lägg även
     kontroll att `handling.arende` inte är borttaget (`if handling.arende.deleted: abort(404)`).
   - `redigera`: `handling = hamta_aktiv_handling(handling_id)`.
   - `ta_bort`: `handling = hamta_aktiv_handling(handling_id)`.
   - `visa`: `handling = hamta_aktiv_handling(handling_id)` (i stället för
     nuvarande `get_or_404`).
   - `ladda_ner`: hämta versionens handling via `hamta_aktiv_handling` om
     möjligt, alternativt kontrollera `version.handling.deleted` och
     `version.handling.arende.deleted`.
4. **`api.py`:** ingen ändring (redan korrekt), men dubbelkolla att
   `hamta_handling` (rad 510) och `ladda_ner_fil` (rad 624) checkar både
   `handling.deleted` och `handling.arende.deleted`. Om `arende.deleted`
   saknas — lägg till.

### Regressionsrisk
- Befintliga tester som råkar skapa borttagna ärenden och sedan modifiera
  dem kommer nu få 404. Sök efter `deleted=True` i tester och justera.
- `ta_bort` som anropas två gånger ger nu 404 andra gången — verifiera att
  inget test förväntar sig idempotens.

### Teststrategi (`tests/test_routes.py`)
- `test_redigera_borttaget_arende ger_404` — skapa ärende, mjuk-radera, POST
  `/arenden/<id>/redigera` → 404.
- `test_byt_status_borttaget_arende ger_404`.
- `test_ny_version_pa_borttagen_handling ger_404`.
- `test_redigera_borttagen_handling ger_404`.
- `test_ta_bort_arende_idempotent_ger_404_andra_gången`.
- `test_ladda_ner_version_pa_borttagen_handling ger_404`.
- `test_ny_handling_pa_borttaget_arende ger_404`.

---

## Uppgift 4 — Loggning av nedladdning (spårbarhet enligt OSL/Arkivlagen)

**Problem:** Filnedladdning loggas inte i `AuditLog`. Vem som hämtat en
(allmän eller sekretessbelagd) handling går inte att spåra.

**Berörda filer:**
- `app/routes/handlingar.py` — `ladda_ner` (rad 232–242)
- `app/routes/api.py` — `ladda_ner_fil` (rad 622–635)

### Steg

1. **`handlingar.py` `ladda_ner`:** före `send_file(...)`, lägg till:
   ```python
   log_action(
       current_user.id,
       "ladda_ner_version",
       "DocumentVersion",
       version.id,
       {
           "filnamn": version.filnamn,
           "arende": version.handling.arende.diarienummer,
           "handling_id": version.handling_id,
           "version_nr": version.version_nr,
           "ip": request.remote_addr,
       },
   )
   db.session.commit()
   ```
2. **`api.py` `ladda_ner_fil`:** samma, men använd `g.api_user.id` i stället
   för `current_user.id`:
   ```python
   user = _check_auth()
   ...
   log_action(
       user.id,
       "ladda_ner_version",
       "DocumentVersion",
       version.id,
       {
           "filnamn": version.filnamn,
           "arende": version.handling.arende.diarienummer,
           "handling_id": version.handling_id,
           "version_nr": version.version_nr,
           "ip": request.remote_addr,
           "via": "api",
       },
   )
   db.session.commit()
   ```
3. Verifiera att `log_action` + `commit` inte krockar med `send_file` (som
   streamar efter responsen). Överväg att logga **innan** `send_file`
   returneras så att loggen skrivs även om klienten avbryter. Vid fel i
   `_har_sekretessbehorighet` ska ingen logg skrivas (den abortar först).

### Regressionsrisk
- Minimal. Enda risken är att `commit` misslyckas och ger 500 före
  nedladdning. Wrappa logiken i try/except om det är kritiskt, men föredra
  att låta fel bubbla (synlighet > tystnad).

### Teststrategi (`tests/test_routes.py`)
- `test_ladda_ner_loggas_i_audit_log` — skapa handling + version, logga in,
  GET `/handlingar/ladda-ner/<version_id>`, verifiera att en `AuditLog`-rad
  med `action == "ladda_ner_version"` finns för `current_user.id`.
- `test_ladda_ner_nekas_och_loggas_inte_for_observator_sekretess` —
  observatör får 403 vid sekretesshandling och ingen loggpost skapas.
- API-motsvarighet: `test_api_ladda_ner_fil_loggas`.

---

## Uppgift 5 — Arkivarie kan arkivera ärenden

**Problem:** `byt_status` (både webb och API) tillåter inte `arkivarie`,
trots att rollen ges sekretessaccess för `status == "arkiverat"` och
`STATUS_FLOW` tillåter `avslutat → arkiverat`.

**Berörda filer:**
- `app/routes/arenden.py` — `byt_status` (rad 121–146)
- `app/routes/api.py` — `byt_status` (rad 338–373)
- `app/templates/arenden/visa.html` — statusknappar (rad 32)

### Steg

1. **`arenden.py` `byt_status`:** ändra decorator till
   `@role_required("admin", "registrator", "handlaggare", "arkivarie")`.
   Lägg sedan till en begränsning i funktionskroppen (efter `ny_status` hämtats,
   före statussättningen):
   ```python
   if current_user.role == "arkivarie":
       # Arkivarie får ENDAST gå TILL arkiverat — inte ändra annat.
       if ny_status != "arkiverat":
           abort(403)
   ```
   Bevara den befintliga handläggare-äganderkontrollen.
2. **`api.py` `byt_status`:** lägg till `"arkivarie"` i `_check_auth(...)`-anropet
   (rad 344). Lägg till samma begränsning:
   ```python
   if user.role == "arkivarie" and ny_status != "arkiverat":
       smorest_abort(403, message="Arkivarie får endast arkivera ärenden.")
   ```
3. **`app/templates/arenden/visa.html` (rad 32):** utöka villkoret så att
   arkivarie ser knappen "Arkivera" när `arende.status == "avslutat"`:
   ```
   {% if arende.allowed_transitions and (
       current_user.role in ('admin', 'registrator')
       or (current_user.role == 'handlaggare' and arende.handlaggare_id == current_user.id)
       or (current_user.role == 'arkivarie' and 'arkiverat' in arende.allowed_transitions)
   ) %}
   ```
   För arkivarie bör endast `arkiverat`-knappen renderas — filtrera
   `arende.allowed_transitions` i loopen om rollen är `arkivarie`:
   ```
   {% set trans = arende.allowed_transitions if current_user.role != 'arkivarie' else arende.allowed_transitions|reject('ne', 'arkiverat')|list %}
   {% for s in trans %}
   ```
   (eller enklare: en `{% if s == 'arkiverat' or current_user.role != 'arkivarie' %}`-check inuti loopen).

### Regressionsrisk
- Befintliga `byt_status`-tester för admin/registrator/handlaggare ska
  fortsätta passera oförändrat.
- Arkivarie som tidigare fick 403/redirect vid arkiveringsförsök får nu 200.
- Verifiera att `STATUS_FLOW["arkiverat"] == []` (terminalstatus) —
  arkivarie kan inte låsa upp.

### Teststrategi (`tests/test_routes.py`)
- `test_arkivarie_kan_arkivera_avslutat_arende` — ärende med status
  `avslutat`, logga in som arkivarie, POST `/arenden/<id>/status` med
  `ny_status=arkiverat` → status ändrad, `AuditLog` skriven.
- `test_arkivarie_nekas_annan_status_andring` — arkivarie POST med
  `ny_status=pagaende` → 403.
- `test_arkivarie_kan_inte_arkivera_oppnat_arende` — status `oppnat`,
  arkivarie POST `arkiverat` → ogiltig övergång (404/redirect med flash,
  eftersom `arkiverat` inte är i `allowed_transitions` för `oppnat`).
- API-motsvarigheter: `test_api_arkivarie_kan_arkivera`,
  `test_api_arkivarie_nekas_annan_status`.

---

## Validering

Kör hela testsviten efter varje uppgift och innan nästa påbörjas:

```bash
source .venv/bin/activate
pytest tests/ -v
```

Specifika testfall som ska passera efter respektive uppgift:

- **Uppgift 1:** `tests/test_routes.py::TestObservator::test_observator_lista_exkluderar_sekretess_arenden`
  och dashboard-motsvarigheter.
- **Uppgift 2:** `tests/test_models.py` — alla `Nummerserie`-tester + nya
  `test_next_number_ar_unik_vid_kapplöpning`.
- **Uppgift 3:** nya `test_*_borttaget_*_ger_404`-tester.
- **Uppgift 4:** `test_ladda_ner_loggas_i_audit_log` +
  `test_ladda_ner_nekas_och_loggas_inte_for_observator_sekretess`.
- **Uppgift 5:** `test_arkivarie_kan_arkivera_avslutat_arende` +
  `test_arkivarie_nekas_annan_status_andring`.

Efter alla fem:
```bash
pytest tests/ -v --tb=short
```
Förväntat: 0 misslyckade. Rapportera eventuella återstående fel med
fil:test innan nästa fas.

---

## Kommande planer (ej i denna plan)

Följande uppgifter identifierades i granskningen men hanteras i senare
planer. Designval ovan har beaktat dem:

- **6–10 (MEDEL):** sök-läcka för `avsandare`/`beskrivning`, `SECRET_KEY`-
  fallback, sekretess-diff i logg, API rate limits, AuditLog oföränderlighet.
  - Uppgift 1:s `Arende.sekretess_filter`-hjälpfunktion kommer att återanvändas
    i sökfixen (uppgift 6).
- **11–18 (LÅG):** duplicerad söklogik, ohanterade ValueError/KeyError,
  validering av `role`/`typ`, N+1-frågor, stora blobbar, legacy `Query.get`,
  hårdkodad e-post, f-string i SQL.
  - Uppgift 3:s `app/services.py` blir en naturlig hem för framtida
    servicefunktioner (t.ex. sök-service i uppgift 11).
- **19–27 (tester):** testluckor — sekretess i lista/dashboard, race på
  Nummerserie, deleted-respektering, `ladda_ner` 403, `exportera`,
  `byt_losenord`, admin-routes, API-endpoints utöver `/brukare`.
  - Flera tester i denna plan (1, 2, 3, 4) stänger motsvarande luckor i
    grupp 19–27; notera vilka som redan täcks så de inte dupliceras i nästa
    plan.
