# ShiftSync

ShiftSync turns work rosters into Google Calendar events. The current version supports user accounts, saved roster/workplace preferences, per-user Google Calendar connections, duplicate protection, and an installable mobile-first PWA.

## Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

The default database is SQLite at `backend/shiftsync.db`. To use PostgreSQL, set `DATABASE_URL`, for example:

```text
postgresql+psycopg://user:password@host:5432/shiftsync
```

## Frontend

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`.

## Google Calendar OAuth

Enable the Google Calendar API, create a Web OAuth client, and configure this redirect URI:

```text
http://localhost:8000/google/callback
```

Place the downloaded OAuth client JSON at `backend/client_secret.json`.

## Accounts and preferences

Create an account in the UI. Under Account settings, save:

- display name
- name as shown on the roster
- default event title
- default workplace/location
- timezone

These preferences are stored with the account instead of only in the current browser.

## Android / PWA

The frontend includes a web app manifest and service worker. Once deployed over HTTPS, open ShiftSync in Chrome on Android and use **Add to Home screen / Install app**. It launches in a standalone app window.

For Play Store distribution later, the same frontend can be wrapped with Capacitor or a Trusted Web Activity.

## Production notes

- Use PostgreSQL rather than local SQLite on hosts with ephemeral filesystems.
- Set a long random `SESSION_SECRET`.
- Set `COOKIE_SECURE=true` when the frontend/backend are served over HTTPS.
- Google OAuth refresh credentials are currently stored in the database. Encrypt that column before treating this as a production multi-user service.
