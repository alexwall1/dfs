# DFS

Ett enkelt och öppet (GPL) diarieföringssystem. Stödjer olika användarroller, diarieföring av olika filversioner kopplade till handlingar, API, en AI-registrator som diarieför enligt mejl-instruktioner, export till Excel m.m. (Se användarhandbok.) 

![alt text](https://github.com/alexwall1/dfs/blob/main/screenshot.png?raw=true)

# Driftsättning

## Förutsättningar

- Docker och Docker Compose installerade på servern
- Git för att hämta koden

## Steg-för-steg

### 1. Hämta koden

```bash
git clone <repo-url> dfs
cd dfs
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
