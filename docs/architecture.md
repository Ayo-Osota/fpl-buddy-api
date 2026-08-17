# Architecture

## connect-fpl-team

Scope: per-entry (one connected user's own team) data - connecting an FPL
Team ID and reading that team's squad/history/budget.

```
                    ┌───────────────────────┐
   (future change)  │   fpl-buddy-fe         │   static demo today;
                     │   React/Vite           │   not wired to this API yet
                     └───────────┬───────────┘
                                 │ HTTPS/JSON (not built in this change)
                                 ▼
                     ┌───────────────────────┐
                     │   fpl-buddy-api        │   Django
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
```

### Notes

- **No `django.contrib.auth`.** The `accounts.User` model is keyed on a
  public FPL Team ID, not a password — see connect-fpl-team's design.md
  Decision 3. Sessions use Django's built-in DB-backed session framework
  (`django_session` table) directly, independent of `contrib.auth`.
- **Local Postgres for dev**: `docker run -p 5432:5432 -e POSTGRES_USER=fpl_buddy
  -e POSTGRES_PASSWORD=fpl_buddy -e POSTGRES_DB=fpl_buddy postgres:16` (or see
  `docker-compose.yml` in this directory).
- **Deployment target is Railway** (design.md Decision 7), but no
  provisioning is done in that change — `settings.py` reads `DATABASE_URL`
  when present (Railway's convention) so a future deploy doesn't require a
  settings rewrite.

---

## suggest-best-squad

Scope: a deterministic (no LLM) pipeline that ingests the league-wide FPL
player pool, scores every player, and selects a legal 15-man squad per
named strategy — ingestion → scoring → ILP optimization → starting
XI/captains → persistence, plus a backtest harness to measure whether any
of it actually works. See openspec's `suggest-best-squad` change for the
full proposal/design/specs.

```
┌─ INGESTION (fpl_data/ingestion.py) ───────────────────────────────────┐
│  FplClient.get_bootstrap_static()/get_fixtures()/get_element_summary()│
│  → Team, Player (full pool, always refreshed)                        │
│  → Fixture (global fixture list)                                     │
│  → PlayerGameweekHistory, PlayerSeasonHistory                        │
│    (per-player summaries - staleness-aware, skips zero-availability   │
│     players, commits per player so an interrupted run is resumable)   │
│                                                                        │
│  manage.py ingest_fpl_data [--force-refresh]                          │
└──────────────────────────────┬─────────────────────────────────────────┘
                                ▼
┌─ SCORING (fpl_data/scoring/) ─────────────────────────────────────────┐
│  availability.py   status/fitness multiplier, discipline factor       │
│  fixtures.py        team-strength resolution, horizon-bounded         │
│                      difficulty, double/blank-gameweek detection      │
│  performance.py     per-gameweek score, past-season aggregate,        │
│                      set-piece/ownership/rotation components,         │
│                      preseason no-history handling                    │
│  engine.py           combines components x strategy weights           │
│                      -> total_score (fpl_data/strategies.py)          │
└──────────────────────────────┬─────────────────────────────────────────┘
                                ▼
┌─ SELECTION ────────────────────────────────────────────────────────────┐
│  optimization.py (ILP via pulp)   15-man squad: budget/quota/club     │
│                                     constraints + strategy hard        │
│                                     constraints, by construction        │
│  selection.py                     best of 8 legal formations,          │
│                                     ranked bench, 3 captain picks       │
│                                     (season / next-gw / differential)  │
└──────────────────────────────┬─────────────────────────────────────────┘
                                ▼
┌─ PERSISTENCE (fpl_data/persistence.py) ────────────────────────────────┐
│  ScoringRun + PlayerScore   one row per player per run, every          │
│                              component kept (not just the total)       │
│  SuggestedSquad +            starters/bench/formation/captains,        │
│  SuggestedSquadPlayer        one row per strategy per run              │
│                                                                          │
│  manage.py score_players [--strategy NAME]                             │
│  manage.py build_squads [--strategy NAME]                              │
└──────────────────────────────┬─────────────────────────────────────────┘
                                ▼
┌─ READ-ONLY API (fpl_data/views.py) ─────────────────────────────────────┐
│  GET /players/scores/?strategy=    latest run's per-player scores      │
│  GET /players/shortlist/?strategy= per-position ranked shortlist       │
│  GET /squads/?strategy=            latest suggested squad(s)           │
│  No session required; never contacts FPL or the solver.                │
└───────────────────────────────────────────────────────────────────────┘

┌─ BACKTESTING (fpl_data/backtesting.py) ────────────────────────────────┐
│  score_pool_as_of(strategy, season, replay_gw)                         │
│    -> scores using only PlayerGameweekHistory rounds < replay_gw        │
│  run_backtest(...) -> per-replay-point: squad's realized points vs.    │
│    random/template(highest-ownership)/price-only baselines,             │
│    plus captain-outscored-median accuracy (season/next-gw/differential)│
│  ablate(strategy, factor) -> strategy copy with one coefficient zeroed  │
│                                                                          │
│  manage.py backtest --strategy NAME --from-event N --to-event M        │
│                      [--ablate FACTOR] [--season-start-year Y]         │
└───────────────────────────────────────────────────────────────────────┘
```

### Notes

- **No LLM anywhere in this pipeline.** Scoring, selection, and
  backtesting are fully deterministic - see the change's proposal.md for
  why (establishing whether the deterministic signal is strong enough is
  the point of this phase).
- **ILP, not greedy selection** (design.md Decision 1). `pulp`'s CBC
  solver enforces budget/position-quota/club-limit constraints
  simultaneously, so every produced squad is legal by construction and
  infeasibility is reported (`InfeasibleStrategyError`) rather than
  approximated.
- **Global data is normalized**, not raw JSONB like `FplDataCache`
  (design.md Decision 2) - `Team`/`Player`/`PlayerGameweekHistory`/
  `PlayerSeasonHistory`/`Fixture` are real columns because this data is
  filtered, sorted, and joined, not just replayed back to one user.
- **Strategies are hardcoded**, in `fpl_data/strategies.py`
  (`balanced`, `premium_heavy`, `differential`, `set_and_forget`) - not
  stored in the database or accepted from API input (design.md Decision
  4). Each strategy is an objective-coefficient vector over exactly the
  seven ablatable scoring factors, plus a fixture horizon and optional
  hard constraints.
- **Preseason degrades explicitly.** With zero current-season gameweeks
  played, `compute_player_components` falls back to past-season history
  entirely rather than dividing by zero games — confirmed against live
  data on 2026-08-17 (verification run: 590 players ingested, 0 current-
  season `PlayerGameweekHistory` rows, scoring/selection/persistence still
  produced legal squads for all 4 strategies).
- **Backtesting against a real historical season is not yet possible.**
  FPL's `element-summary` endpoint only exposes per-gameweek history
  (`history`) for the *current* season; completed seasons are only
  available as season-level aggregates (`history_past`), with no
  gameweek-by-gameweek breakdown. Combined with 2026/27 being brand new at
  the time this change was built (zero gameweeks played yet), there is no
  real per-gameweek data to replay against. The backtest harness itself is
  verified with 17 tests against synthetic gameweek data (point-in-time
  correctness, baseline legality, ablation, captain accuracy) - a real
  full-season sweep becomes possible once this season has played out, or
  if a future change adds a historical per-gameweek data source.
