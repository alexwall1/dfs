# Driftsättning av DFS2

## Förutsättningar

- Docker och Docker Compose installerade på servern
- Git för att hämta koden

## Steg-för-steg

### 1. Hämta koden

```bash
git clone <repo-url> dfs2
cd dfs2
```

### 2. Konfigurera miljövariabler

```bash
cp .env.example .env
```

Redigera `.env` och sätt minst:

```
POSTGRES_USER=dfs
POSTGRES_DB=dfs
SESSION_COOKIE_SECURE=true
```

### 3. Skapa hemliga nycklar

Kopiera exempelfilerna och fyll i riktiga värden:

```bash
cp secrets/db_password.txt.example    secrets/db_password.txt
cp secrets/secret_key.txt.example     secrets/secret_key.txt
cp secrets/admin_password.txt.example secrets/admin_password.txt
```

Redigera varje fil:

| Fil | Innehåll |
|-----|----------|
| `secrets/db_password.txt` | Starkt lösenord för PostgreSQL |
| `secrets/secret_key.txt` | Slumpmässig sträng, minst 32 tecken |
| `secrets/admin_password.txt` | Starkt lösenord för adminanvändaren |

Generera ett säkert `secret_key`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/secret_key.txt
```

### 4. Starta applikationen

```bash
docker compose up --build -d
```

Applikationen startar på port **5000**. Databasen initieras och adminanvändaren skapas automatiskt vid första start.

### 5. Verifiera

```bash
docker compose ps       # Kontrollera att båda tjänster körs
docker compose logs app # Se applikationsloggar
```

Öppna `http://<server-ip>:5000` och logga in med användaren `admin` och lösenordet från `secrets/admin_password.txt`.

## AI-registrator (valfri tjänst)

AI-registratorn lyssnar på inkommande e-post och registrerar handlingar automatiskt via REST API:et.

### 1. Skapa secrets för AI-registratorn

```bash
cp secrets/imap_password.txt.example      secrets/imap_password.txt
cp secrets/smtp_password.txt.example      secrets/smtp_password.txt
cp secrets/openrouter_api_key.txt.example secrets/openrouter_api_key.txt
cp secrets/ai_api_key.txt.example         secrets/ai_api_key.txt
cp secrets/dfs2_api_key.txt.example       secrets/dfs2_api_key.txt
```

| Fil | Innehåll |
|-----|----------|
| `secrets/imap_password.txt` | Lösenord för IMAP-kontot som bevakas |
| `secrets/smtp_password.txt` | Lösenord för SMTP-kontot som skickar bekräftelser |
| `secrets/openrouter_api_key.txt` | API-nyckel för OpenRouter (LLM) |
| `secrets/ai_api_key.txt` | Valfri nyckel för att skydda AI-registratorns egna HTTP-endpoint |
| `secrets/dfs2_api_key.txt` | Nyckel som AI-registratorn använder mot DFS2 REST API |

Generera ett slumpmässigt värde för `dfs2_api_key.txt`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/dfs2_api_key.txt
```

Konfigurera e-post och modell i `.env` (se `.env.example` för alla variabler):

```
IMAP_HOST=imap.example.com
IMAP_USER=registrator@example.com
SMTP_HOST=smtp.example.com
SMTP_USER=registrator@example.com
SMTP_FROM=registrator@example.com
OLLAMA_MODEL=mistral-nemo
```

### 2. Registrera API-nyckeln i databasen

Efter att applikationen startats för första gången måste nyckeln läggas in i databasen. Den ska lagras som ett SHA-256-hash.

```bash
# Beräkna hash av nyckeln
python3 -c "
import hashlib, sys
key = open('secrets/dfs2_api_key.txt').read().strip()
print(hashlib.sha256(key.encode()).hexdigest())
"
```

Kör sedan SQL mot databasen (ersätt `<hash>` med utskriften ovan):

```bash
docker compose exec db psql -U dfs -d dfs -c "
INSERT INTO api_key (user_id, key_hash, label, aktiv, skapad_datum)
SELECT id, '<hash>', 'ai-registrator', true, NOW()
FROM users WHERE username = 'admin' LIMIT 1;
"
```

> **OBS:** Om du kör `docker compose down -v` raderas databasen och detta steg måste göras om efter nästa uppstart.

### 3. Starta med AI-registratorn

```bash
docker compose --profile ai up --build -d
```

## Bakom en reverse proxy (rekommenderat)

Sätt upp nginx eller liknande framför applikationen för HTTPS och korrekt IP-loggning. Lägg till i `.env`:

```
PROXY_COUNT=1
```

## Uppgradering

```bash
git pull
docker compose up --build -d
```

## Stoppa applikationen

```bash
docker compose down
```

Data i databasen bevaras i Docker-volymen `pgdata`. För att även radera data:

```bash
docker compose down -v
```
