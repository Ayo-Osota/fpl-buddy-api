# Architecture — connect-fpl-team

Scope: this diagram covers what the `connect-fpl-team` change actually builds.
It intentionally does not show the future Gemini/backtesting/RAG layers —
those depend on this foundation but aren't part of it (see the openspec
change's proposal.md).

```
                    ┌───────────────────────┐
   (future change)  │   fpl-buddy-fe         │   static demo today;
                     │   React/Vite           │   not wired to this API yet
                     └───────────┬───────────┘
                                 │ HTTPS/JSON (not built in this change)
                                 ▼
                     ┌───────────────────────┐
                     │   fpl-buddy-api        │   Django, this change
                     │   (this service)       │
                     │                        │
                     │  accounts app:         │
                     │   - User model         │
                     │   - connect view       │
                     │   - session resolution │
                     │                        │
                     │  fpl_data app:         │
                     │   - FplDataCache model │
                     │   - FPL client         │
                     │     (rate limit +      │
                     │      backoff)          │
                     │   - squad/history/     │
                     │     budget views       │
                     └───────┬───────────┬────┘
                             │           │
                 ┌───────────▼──┐   ┌────▼──────────────────┐
                 │  Postgres     │   │  FPL public API        │
                 │  (local, via  │   │  (unauthenticated,     │
                 │  Docker for   │   │  undocumented,         │
                 │  dev)         │   │  read-only)             │
                 │                │   │  /api/entry/{id}/       │
                 │  - accounts_   │   │  /api/entry/{id}/       │
                 │    user        │   │    event/{gw}/picks/    │
                 │  - fpl_data_   │   │  /api/entry/{id}/       │
                 │    fpldatacache│   │    history/             │
                 │  - django_     │   └─────────────────────────┘
                 │    session     │
                 └────────────────┘

   ┌────────────────────────┐
   │  fpl-buddy/ (separate,  │   Not touched by this change. Confirmed
   │  untouched CLI script)  │   direction (design.md Decision 1): will be
   │  - own local CSV cache  │   imported as a library into fpl-buddy-api
   │  - own scoring logic    │   by a future change, once main.py is made
   └─────────────────────────┘   import-safe (no top-level fetch/input()).
```

## Notes

- **No `django.contrib.auth`.** The `accounts.User` model is keyed on a
  public FPL Team ID, not a password — see design.md Decision 3. Sessions
  use Django's built-in DB-backed session framework (`django_session` table)
  directly, independent of `contrib.auth`.
- **Local Postgres for dev**: `docker run -p 5432:5432 -e POSTGRES_USER=fpl_buddy
  -e POSTGRES_PASSWORD=fpl_buddy -e POSTGRES_DB=fpl_buddy postgres:16` (or see
  `docker-compose.yml` in this directory).
- **Deployment target is Railway** (design.md Decision 7), but no
  provisioning is done in this change — `settings.py` reads `DATABASE_URL`
  when present (Railway's convention) so a future deploy doesn't require a
  settings rewrite, but this change only runs locally.
