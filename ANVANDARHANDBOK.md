# Användarhandbok — DFS Diarieföringssystem

## Innehåll

1. [Introduktion](#introduktion)
2. [Inloggning](#inloggning)
3. [Översikt (Dashboard)](#översikt)
4. [Ärenden](#ärenden)
5. [Handlingar och dokument](#handlingar-och-dokument)
6. [Sök](#sök)
7. [Arkiv](#arkiv)
8. [Administration](#administration)
9. [Roller och behörigheter](#roller-och-behörigheter)

---

## Introduktion

DFS är ett diarieförings- och ärendehanteringssystem byggt i enlighet med Offentlighets- och sekretesslagen (2009:400) samt Arkivlagen (1990:782). Systemet hanterar registrering av ärenden, handlingar och dokument med fullständig spårbarhet via granskningslogg.

---

## Inloggning

1. Öppna systemet i webbläsaren (normalt `http://localhost:5000`).
2. Ange **användarnamn** och **lösenord**.
3. Klicka **Logga in**.

Inaktiverade konton kan inte logga in. Kontakta administratören om du har problem.

För att logga ut, klicka **Logga ut** i menyraden uppe till höger.

---

## Översikt

Efter inloggning visas en översiktssida med:

- **Antal öppna ärenden** — ärenden med status "Öppnat"
- **Antal pågående ärenden** — ärenden med status "Pågående"
- **Antal avslutade ärenden** — ärenden med status "Avslutat"
- **Senaste ärenden** — de 10 senast skapade ärendena

Handläggare ser dessutom **Mina ärenden** — en lista över ärenden som är tilldelade dem och har status "Öppnat" eller "Pågående".

---

## Ärenden

### Visa ärenden

Klicka **Ärenden** i menyn för att se alla ärenden. Listan visar 20 ärenden per sida och kan filtreras efter status via flikarna.

### Skapa nytt ärende

*Kräver rollen admin eller registrator.*

1. Klicka **Ärenden** → **Nytt ärende**.
2. Fyll i formuläret:
   - **Prefix** — prefix för diarienumret (standard: "DNR"). Numret genereras automatiskt, t.ex. `DNR-2026-0001`.
   - **Ärendemening** — kort beskrivning av ärendet.
   - **Sekretess** — kryssa i om ärendet omfattas av sekretess.
   - **Sekretessgrund** — ange lagrum eller grund för sekretess (vid behov).
   - **Handläggare** — välj en handläggare från listan (valfritt).
3. Klicka **Spara**.

### Redigera ärende

*Kräver rollen admin eller registrator.*

1. Öppna ärendet och klicka **Redigera**.
2. Ändra ärendemening, sekretess, sekretessgrund eller handläggare.
3. Klicka **Spara**.

### Ändra status

*Kräver rollen admin, registrator eller handläggare.*

Ärenden följer ett fastställt statusflöde:

```
Öppnat → Pågående → Avslutat → Arkiverat
                         ↓
                      Pågående (återöppning)
```

Tillåtna övergångar:

| Från | Till |
|------|------|
| Öppnat | Pågående |
| Pågående | Avslutat |
| Avslutat | Arkiverat eller Pågående |
| Arkiverat | *(inga — slutstatus)* |

Klicka på önskad status i ärendevyn för att genomföra övergången.

### Ta bort ärende

*Kräver rollen admin.*

Klicka **Ta bort** i ärendevyn. Ärendet tas bort mjukt (markeras som borttaget men finns kvar i databasen för spårbarhet).

---

## Handlingar och dokument

Handlingar kopplas till ett ärende och representerar inkommande, utgående eller upprättade dokument.

### Registrera ny handling

*Kräver rollen admin, registrator eller handläggare.*

1. Öppna ärendet och klicka **Ny handling**.
2. Fyll i formuläret:
   - **Typ** — välj *Inkommande*, *Utgående* eller *Upprättad*.
   - **Datum inkommen** — datum då handlingen inkom (standard: dagens datum).
   - **Avsändare** — vem handlingen kommer från (valfritt).
   - **Mottagare** — vem handlingen skickas till (valfritt).
   - **Beskrivning** — beskrivning av handlingen.
   - **Sekretess** — kryssa i vid behov.
   - **Fil** — ladda upp en fil (valfritt, max 50 MB).
3. Klicka **Spara**.

### Visa handling

Klicka på en handling i ärendevyn för att se dess information och alla uppladdade versioner.

### Ladda upp ny version

*Kräver rollen admin, registrator eller handläggare.*

1. Öppna handlingen och klicka **Ny version**.
2. Välj fil och ange en valfri kommentar.
3. Klicka **Ladda upp**.

Versionsnumret ökas automatiskt. Alla tidigare versioner finns kvar och kan laddas ner.

### Ladda ner dokument

Klicka på filnamnet eller nedladdningsknappen vid önskad version.

### Ta bort handling

*Kräver rollen admin eller registrator.*

Klicka **Ta bort** i handlingsvyn. Handlingen tas bort mjukt.

---

## Sök

Klicka **Sök** i menyn. Alla inloggade användare kan söka.

Tillgängliga sökfält (alla valfria, kombinera fritt):

| Fält | Beskrivning |
|------|-------------|
| **Diarienummer** | Sök på hela eller delar av diarienumret |
| **Ärendemening** | Fritext i ärendebeskrivningen |
| **Status** | Filtrera på status (Öppnat, Pågående, Avslutat, Arkiverat) |
| **Från datum** | Ärenden skapade från och med detta datum |
| **Till datum** | Ärenden skapade till och med detta datum |
| **Avsändare** | Sök bland avsändare i kopplade handlingar |

Fyll i minst ett fält och klicka **Sök**. Resultatet visar maximalt 100 träffar, sorterade med de nyaste först.

---

## Arkiv

*Kräver rollen admin eller arkivarie.*

Klicka **Arkiv** i menyn för att se alla avslutade och arkiverade ärenden.

### Exportera ärende

1. Klicka **Exportera** bredvid önskat ärende.
2. En JSON-fil laddas ner med ärendets fullständiga information:
   - Ärendeuppgifter (diarienummer, mening, status, sekretess)
   - Alla handlingar med versionshistorik
   - Fullständig granskningslogg

Filnamnet följer formatet `DNR-2026-0001.json`. Exporten innehåller inte fildata (binärfiler) utan metadata om dokumentversioner.

---

## Administration

*Kräver rollen admin.*

Administrationsmenyn nås via **Admin** i menyraden.

### Användare

**Visa användare** — lista över alla användare med namn, roll och status.

**Skapa ny användare:**

1. Klicka **Ny användare**.
2. Fyll i:
   - **Användarnamn** — unikt inloggningsnamn.
   - **Fullständigt namn** — visningsnamn.
   - **E-post** — valfritt.
   - **Roll** — Administratör, Registrator, Handläggare eller Arkivarie.
   - **Lösenord** — initialt lösenord.
3. Klicka **Spara**.

**Redigera användare:**

1. Klicka på en användare i listan.
2. Ändra namn, e-post, roll eller aktiv-status.
3. Lämna lösenordsfältet tomt för att behålla nuvarande lösenord, eller fyll i ett nytt.
4. Klicka **Spara**.

Avaktiverade användare kan inte logga in men finns kvar i systemet.

### Nummerserier

Visar alla genererade diarienummerserier med prefix, år och senaste nummer. Denna vy är enbart för insyn — numren genereras automatiskt vid skapande av nya ärenden.

### Granskningslogg

Visar alla loggade händelser i systemet (50 per sida), sorterade med de senaste först. Varje post innehåller:

- **Händelse** — vad som gjordes (t.ex. skapande, redigering, statusändring)
- **Användare** — vem som utförde handlingen
- **Måltyp och ID** — vilket objekt som påverkades
- **Detaljer** — ytterligare information i JSON-format
- **Tidpunkt** — datum och tid
- **IP-adress** — varifrån handlingen utfördes

---

## Roller och behörigheter

| Funktion | Admin | Registrator | Handläggare | Arkivarie |
|----------|:-----:|:-----------:|:-----------:|:---------:|
| Logga in och se översikt | ✓ | ✓ | ✓ | ✓ |
| Visa ärenden | ✓ | ✓ | ✓ | ✓ |
| Skapa ärende | ✓ | ✓ | | |
| Redigera ärende | ✓ | ✓ | | |
| Ändra status | ✓ | ✓ | ✓ | |
| Ta bort ärende | ✓ | | | |
| Registrera handling | ✓ | ✓ | ✓ | |
| Visa och ladda ner handling | ✓ | ✓ | ✓ | ✓ |
| Ladda upp ny version | ✓ | ✓ | ✓ | |
| Ta bort handling | ✓ | ✓ | | |
| Söka | ✓ | ✓ | ✓ | ✓ |
| Visa arkiv och exportera | ✓ | | | ✓ |
| Hantera användare | ✓ | | | |
| Visa nummerserier | ✓ | | | |
| Visa granskningslogg | ✓ | | | |
