# Implementationsplan – DFS2 Testluckor (Uppgift 19–27)

Denna plan täcker testluckor identifierade i kodbasgranskningen.
**Viktigt:** Flera av dessa luckor stängs redan av teststrategierna i
planerna för uppgift 1–10 (se `IMPLEMENTATION_PLAN.md` och
`IMPLEMENTATION_PLAN_6_10.md`). Denna plan fokuserar på de luckor som
**inte** täcks där, samt konsoliderar/undviker duplicering av tester som
redan specificerats.

## Översikt — vad täcks var

| Lucka | Täcks redan av | Status i denna plan |
|-------|----------------|---------------------|
| 19. Sekretess i lista/dashboard | Uppgift 1 (plan 1–5) | **Redan täckt** — verifiera, duplicera ej |
| 20. Nummerserie race | Uppgift 2 (plan 1–5) | **Redan täckt** — verifiera |
| 21. deleted-ärende redigera/status | Uppgift 3 (plan 1–5) | **Redan täckt** — verifiera |
| 22. ladda_ner 403 | Uppgift 4 (plan 1–5) | **Redan täckt** — verifiera, ev. utöka |
| 23. arenden.exportera (XLSX) | Ej täckt | **Ny** |
| 24. byt_losenord route | Ej täckt | **Ny** |
| 25. Admin: nummerserier/api_nycklar/hjalp | Ej täckt | **Ny** |
| 26. ta_bort_anvandare "sista admin" | Svagt täckt | **Utöka** |
| 27. API-endpoints utöver /brukare | Ej täckt | **Ny** |

---

## Uppgift 19–22 — Verifiera tester från plan 1–5 finns

Innan nya tester skrivs, verifiera att testerna specificerade i
`IMPLEMENTATION_PLAN.md` (uppgift 1–5) faktiskt implementerades:

### 19. Sekretess i lista/dashboard
- `test_observator_lista_exkluderar_sekretess_arenden`
- `test_observator_dashboard_senaste_exkluderar_sekretess`
- `test_observator_dashboard_stats_exkluderar_sekretess`
- `test_handlaggare_lista_ser_egen_sekretess_men_inte_andras`

**Om saknas:** implementera enligt plan 1–5. **Om finns:** inget att göra.

### 20. Nummerserie race
- `test_next_number_ar_unik_vid_kapplöpning` (i `test_models.py`)

**Om saknas:** implementera. **Om finns:** inget att göra.

### 21. deleted-ärende redigera/status
- `test_redigera_borttaget_arende_ger_404`
- `test_byt_status_borttaget_arende_ger_404`
- `test_ny_version_pa_borttagen_handling_ger_404`
- `test_ladda_ner_version_pa_borttagen_handling_ger_404`
- `test_ny_handling_pa_borttaget_arende_ger_404`

**Om saknas:** implementera. **Om finns:** inget att göra.

### 22. ladda_ner 403 + loggning
- `test_ladda_ner_loggas_i_audit_log`
- `test_ladda_ner_nekas_och_loggas_inte_for_observator_sekretess`
- `test_api_ladda_ner_fil_loggas`

**Om saknas:** implementera. **Utöka med:** `test_ladda_ner_nekas_for_handlaggare_som_inte_ager_arende`
(handläggare B försöker ladda ner handling från handläggare A:s sekretessärende → 403).

---

## Uppgift 23 — `arenden.exportera` (XLSX) otestad

**Problem:** `app/routes/arenden.py:exportera` (rad 149–191) saknar tester.
Genererar XLSX med `openpyxl`; risk för fel i kolumnlayout, N+1 (adresseras
i uppgift 14), och RBAC (ska kräva admin/registrator).

**Berörd fil:** `tests/test_routes.py` (ny testklass `TestExporteraArenden`).

### Steg

1. **Skapa testklass** med setup som skapar 2–3 ärenden med olika status.
2. **Testfall:**
   - `test_exportera_kraver_admin_eller_registrator` — handläggare/observatör
     → 403/redirect.
   - `test_exportera_returnerar_xlsx` — admin GET `/arenden/exportera` →
     200, `Content-Type` innehåller `spreadsheetml`, innehåll börjar med
     XLSX-magiska byte (`PK`).
   - `test_exportera_filtrerar_pa_status` — `?status=avslutat` → endast
     avslutade ärenden i arket.
   - `test_exportera_innehaller_rubriker` — parsar XLSX med `openpyxl.load_workbook`
     och verifiera rad 1 = `["Diarienummer", "Ärende", "Status",
     "Handläggare", "Skapad"]`.
   - `test_exportera_exkluderar_borttagna_arenden` — mjuk-radera ett ärende,
     verifiera att det inte finns med.
3. **Hjälpfunktion** för att pars XLSX-svar:
   ```python
   import io
   import openpyxl
   def _parse_xlsx(resp):
       return openpyxl.load_workbook(io.BytesIO(resp.data)).active
   ```

### Regressionsrisk
- Inga kodändringar; endast tester. Låg risk.

---

## Uppgift 24 — `byt_losenord` route otestad

**Problem:** `app/auth.py:byt_losenord` (rad 235–265) saknar tester.
Innehåller lösenordsvalidering, gammalt-lösenord-kontroll, och
`tvinga_byta_losenord`-flagga.

**Berörd fil:** `tests/test_routes.py` (ny testklass `TestBytLosenord`).

### Steg

Läs `auth.py:235–265` först för att bekräfta flödet. Testfall:
1. `test_byt_losenord_kraver_inloggning` — GET utan login → redirect till `/login`.
2. `test_byt_losenord_get_visar_formular` — inloggad GET → 200.
3. `test_byt_losenord_korrekt_uppdaterar` — POST med giltigt nytt lösenord
   (12+ tecken, versal, gemen, siffra, specialtecken) → 302, lösenord ändrat
   (`user.check_password(nytt)` är True), `AuditLog`-post `byt_losenord`.
4. `test_byt_losenord_svagt_losenord_visar_fel` — POST med `password` (för kort)
   → 200 + flash, lösenord oförändrat.
5. `test_byt_losenord_fel_gammalt_losenord` — POST med fel `gammalt_losenord`
   → 200 + flash, lösenord oförändrat.
6. (Om `maste_byta_losenord`-flöde finns i route) `test_byt_losenord_avmarkerar_maste_byta`
   — användare med `maste_byta_losenord=True`, byt lösenord → flaggan False.

### Regressionsrisk
- Inga kodändringar.

---

## Uppgift 25 — Admin-routes otestade: nummerserier, api_nycklar, hjalp

**Problem:** Följande admin-routes saknar tester eller har svag täckning:
- `admin.py:nummerserier` (rad 144–167) — GET-lista + POST ändra prefix.
- `admin.py:api_nycklar` (rad 227–237) — GET-lista.
- `admin.py:ny_api_nyckel` (rad 240–274) — POST skapa.
- `admin.py:äterkalla_api_nyckel` (rad 277–291) — POST återkalla.
- `admin.py:logg` (rad 216–224) — GET paginerad logg (troligen testad,
  verifiera).
- Saknas ev. `hjalp`-route (sök efter `@admin_bp.route("/hjalp")` — om den
  inte finns, hoppa över).

**Berörd fil:** `tests/test_routes.py` (ny testklass `TestAdminNummerserier`,
`TestAdminApiNycklar`).

### Steg — Testfall

**Nummerserier:**
1. `test_nummerserier_get_kraver_admin` — icke-admin → 403/redirect.
2. `test_nummerserier_get_visar_serier` — admin GET → 200, innehåller
   befintlig serie.
3. `test_nummerserier_post_andrar_standardprefix` — admin POST med
   `standardprefix=ABC` → 302, `Installning.get("standardprefix") == "ABC"`,
   `AuditLog` `andra_standardprefix`.
4. `test_nummerserier_post_tom_prefix_anvander_default` — POST tomt →
   prefix sätts till `"DNR"`.

**API-nycklar:**
5. `test_api_nycklar_get_kraver_admin`.
6. `test_api_nycklar_get_visar_befintliga_nycklar`.
7. `test_ny_api_nyckel_skapar_och_visar_raw_key` — admin POST `label=X&user_id=Y`
   → 302, flash innehåller raw key, `APIKey`-rad skapad med korrekt
   `key_hash` (sha256 av raw key), `AuditLog` `skapa_api_nyckel`.
8. `test_ny_api_nyckel_utan_label_visar_fel` — POST utan label → redirect,
   ingen nyckel skapad.
9. `test_ny_api_nyckel_ogiltig_user_visar_fel` — POST med `user_id=999` →
   fel, ingen nyckel.
10. `test_äterkalla_api_nyckel_satter_aktiv_false` — skapa nyckel, POST
    återkalla → `APIKey.aktiv == False`, `AuditLog` `äterkalla_api_nyckel`.
11. `test_äterkallad_nyckel_fungerar_inte_i_api` — skapa + återkalla nyckel,
    försök anropa `/api/v1/arenden` med den → 401.

### Regressionsrisk
- Inga kodändringar.

---

## Uppgift 26 — `ta_bort_anvandare` "sista admin"-scenario svagt

**Problem:** `admin.py:ta_bort_anvandare` (rad 67–102) har logik för att
förhindra borttagning av sista aktiva admin (rad 80–89). Befintligt test
(sök efter `test_ta_bort_*admin*` i `test_routes.py`) tar ev. bort sig
självt eller testar inte det verkliga "sista admin"-scenariot med två
användare.

**Berörd fil:** `tests/test_routes.py` (utöka befintlig `TestAdminAnvandare`).

### Steg — Testfall

1. `test_ta_bort_sista_admin_nekas` — skapa admin A (inloggad) och admin B;
   ta bort B — OK. Ta sedan bort A (sista) — nekas med flash "måste finnas
   minst en aktiv administratör". A kvarstår.
2. `test_ta_bort_admin_nar_flera_finns_ok` — tre admins; ta bort en → OK,
   `deleted=True`, `active=False`, `AuditLog` `ta_bort_anvandare`.
3. `test_ta_bort_sig_själv_nekas` — admin försöker ta bort sig själv →
   nekas ("kan inte ta bort ditt eget konto").
4. `test_ta_bort_inaktiv_admin_raknas_inte_som_sista` — admin A aktiv,
   admin B `active=False`; ta bort A → nekas (B räknas inte trots att
   ej `deleted`). Verifiera logiken i `admin.py:80–86` (filtrerar på
   `active == True`).
5. `test_ta_bort_borttagen_anvandare_idempotent` — ta bort användare två
   gånger → andra gången flash "redan borttagen".

### Regressionsrisk
- Inga kodändringar; eventuellt avslöjar testerna en bugg — då bugfix
  i `admin.py` separat.

---

## Uppgift 27 — API-endpoints utöver /brukare otestade

**Problem:** `app/routes/api.py` är CSRF-exempt och hanterar alla
ärenden/handlingar/versioner-operationer, men bara `/api/v1/brukare` är
testat. Hela API-ytan behöver integrationstester.

**Berörd fil:** `tests/test_routes.py` (ny testklass `TestApi`).

### Förutsättningar
- Skapa en helper `skapa_api_nyckel(db, user, label="test")` som
  returnerar raw key (använd `secrets.token_urlsafe(32)` + hash enligt
  `api.py:46`).
- Helper `api_auth_header(raw_key)` → `{"Authorization": f"Bearer {raw_key}"}`.

### Steg — Testfall per endpoint

**Autentisering:**
1. `test_api_utan_auth_header ger_401`.
2. `test_api_ogiltig_nyckel ger_401`.
3. `test_api_inaktiv_nyckel ger_401` (återkallad).
4. `test_api_inaktiv_user ger_401` (`user.active=False`).
5. `test_api_borttagen_user ger_401` (`user.deleted=True`).
6. `test_api_otillracklig_roll ger_403` — observatör försöker POST `/arenden`.

**Ärenden (GET/POST/PUT/status):**
7. `test_api_lista_arenden_paginering` — skapa 25 ärenden, GET
   `/arenden?page=2` → 5 ärenden, `total=25`, `sidor=5`, `sida=2`.
8. `test_api_lista_arenden_sekretessfilter_observator` — sekretessärende
   syns ej för observatör-nyckel.
9. `test_api_lista_arenden_sekretessfilter_handlaggare` — handläggare ser
   egna sekretess + alla öppna.
10. `test_api_skapa_arende` — POST `/arenden` med giltig body → 201,
    `diarienummer` genererat, `AuditLog` `skapa_arende` med `via=api`.
11. `test_api_skapa_arende_admin_kan_satta_prefix` — admin-nyckel med
    `prefix=TEST` → `diarienummer` börjar med `TEST-`.
12. `test_api_skapa_arende_icke_admin_ignorerar_prefix` — registrator med
    `prefix=TEST` → använder standardprefix.
13. `test_api_hamta_arende` — GET `/arenden/<id>` → 200 med handlingar.
14. `test_api_hamta_arende_borttagen ger_404`.
15. `test_api_hamta_arende_sekretess_ger_403_for_observator`.
16. `test_api_redigera_arende` — PUT med `arende_mening` → 200, uppdaterat.
17. `test_api_byt_status_giltig_overgang` — POST `/status` `ny_status=pagaende`
    → 200.
18. `test_api_byt_status_ogiltig_overgang ger_422`.
19. `test_api_byt_status_arkivarie_kan_arkivera` (från uppgift 5).
20. `test_api_byt_status_arkivarie_nekas_annan ger_403` (från uppgift 5).

**Handlingar/versioner:**
21. `test_api_skapa_handling_utan_fil` — POST multipart utan `fil` → 201,
    handling skapad, ingen version.
22. `test_api_skapa_handling_med_fil` — POST med giltig PDF → 201, version 1.
23. `test_api_skapa_handling_ogiltig_typ ger_422`.
24. `test_api_skapa_handling_pa_borttaget_arende ger_404` (från uppgift 3).
25. `test_api_hamta_handling`.
26. `test_api_hamta_handling_borttagen ger_404`.
27. `test_api_hamta_handling_sekretess ger_403`.
28. `test_api_redigera_handling`.
29. `test_api_ladda_upp_version` — POST `/handlingar/<id>/versioner` med fil
    → 201, `version_nr=2`.
30. `test_api_ladda_ner_fil` — GET `/versioner/<id>/fil` → 200, innehåll =
    uppladdad fil.
31. `test_api_ladda_ner_fil_sekretess ger_403`.
32. `test_api_ladda_ner_fil_loggas` (från uppgift 4).

**Befintligt `/brukare`:** verifiera att befintliga tester fortfarande
passerar; utöka med:
33. `test_api_brukare_kraver_admin_eller_registrator` — handläggare-nyckel → 403.
34. `test_api_brukare_saknad_email ger_422` (Marshmallow-validering).

### Regressionsrisk
- Inga kodändringar. Men: CSRF är av i tester (`conftest.py:21`), så
  API-tester kan POSTa utan CSRF-token. Rate limits är av
  (`conftest.py:17`) så uppgift 9:s limits påverkar inte.

---

## Validering

Efter implementering av alla nya tester:

```bash
source .venv/bin/activate
pytest tests/ -v --tb=short
```

Förväntat: 0 misslyckade. Om ett test avslöjar en bugg (särskilt i
uppgift 26 eller 27), dokumentera buggen och skapa en separat fix-uppgift
— låt inte testet ligga som `xfail` utan notering.

Specifikt för API-tester (uppgift 27): kör med `pytest tests/test_routes.py::TestApi -v`
och verifiera coverage. Överväg `pytest --cov=app/routes/api` om
coverage-verktyg är installerat.

---

## Sammanfattning — vad som täcks var

- **Uppgift 19–22:** verifiera/testa om redan implementerade i plan 1–5.
- **Uppgift 23:** XLSX-export — 5 tester.
- **Uppgift 24:** byt_losenord — 6 tester.
- **Uppgift 25:** admin nummerserier/api_nycklar — 11 tester.
- **Uppgift 26:** sista admin-scenario — 5 tester.
- **Uppgift 27:** API-endpoints — ~34 tester.

Totalt ~55–60 nya tester. Efter detta bör testtäckningen för routes/API
vara betydligt starkare.

---

## Kommande planer (ej i denna plan)

- Spår B (filystemslagring) från uppgift 15 — egen plan.
- Per-API-nyckel rate limits (från uppgift 9).
- AuditLog-serialisering (från uppgift 10).
- Templates-granskning (XSS/`|safe`) — inte fullt täckt i någon plan;
  överväg en separat granskning av `app/templates/`.
