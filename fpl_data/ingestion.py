"""
Ingestion of global (league-wide) FPL data - bootstrap-static, fixtures,
and per-player element-summaries - into the Team/Player/Fixture/
PlayerGameweekHistory/PlayerSeasonHistory models.

Deliberately separate from services.py, which is the per-entry (one
connected user's team) cache-read path from the earlier connect-fpl-team
change. This module writes global data that has no per-user key.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .fpl_client import client
from .models import (
    Fixture,
    Player,
    PlayerGameweekHistory,
    PlayerSeasonHistory,
    Team,
)
from .scoring.availability import availability_multiplier

logger = logging.getLogger(__name__)


def derive_current_season_start_year(bootstrap_static):
    """
    FPL's bootstrap-static has no explicit "current season" field - derive
    it from the first gameweek's deadline. The Premier League season starts
    in July/August, so a deadline in Jan-Jun belongs to the season that
    started the *previous* calendar year; a deadline in Jul-Dec belongs to
    the season starting that year.

    This is what CURRENT_SEASON was hardcoded to in the prototype
    (fpl-buddy/main.py) - here it's derived so it doesn't go stale.
    """
    events = bootstrap_static.get("events") or []
    if not events:
        raise ValueError("bootstrap-static returned no events to derive a season from")

    first_deadline = events[0]["deadline_time"]
    # FPL timestamps are ISO 8601 with a trailing Z.
    year = int(first_deadline[:4])
    month = int(first_deadline[5:7])
    return year if month >= 7 else year - 1


def _is_stale(fetched_at):
    if fetched_at is None:
        return True
    age = timezone.now() - fetched_at
    return age >= timedelta(seconds=settings.FPL_GLOBAL_DATA_FRESHNESS_SECONDS)


def ingest_teams(bootstrap_static):
    """Upsert every team from bootstrap-static. Cheap enough to always run
    in full - no per-team staleness tracking needed."""
    teams_payload = bootstrap_static.get("teams", [])
    team_ids = []
    for t in teams_payload:
        Team.objects.update_or_create(
            id=t["id"],
            defaults={
                "code": t["code"],
                "name": t["name"],
                "short_name": t["short_name"],
                "strength": t.get("strength") or 0,
                "strength_overall_home": t.get("strength_overall_home") or 0,
                "strength_overall_away": t.get("strength_overall_away") or 0,
                "strength_attack_home": t.get("strength_attack_home") or 0,
                "strength_attack_away": t.get("strength_attack_away") or 0,
                "strength_defence_home": t.get("strength_defence_home") or 0,
                "strength_defence_away": t.get("strength_defence_away") or 0,
            },
        )
        team_ids.append(t["id"])
    return team_ids


_PLAYER_DEFAULT_FIELDS = (
    "code",
    "first_name",
    "second_name",
    "web_name",
    "element_type",
    "now_cost",
    "status",
    "chance_of_playing_next_round",
    "news",
    "selected_by_percent",
    "total_points",
    "form",
    "points_per_game",
    "minutes",
    "starts",
    "starts_per_90",
    "goals_scored",
    "assists",
    "clean_sheets",
    "goals_conceded",
    "own_goals",
    "penalties_saved",
    "penalties_missed",
    "saves",
    "bonus",
    "yellow_cards",
    "red_cards",
    "influence",
    "creativity",
    "threat",
    "ict_index",
    "influence_rank",
    "creativity_rank",
    "threat_rank",
    "ict_index_rank",
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    "expected_goals_per_90",
    "expected_assists_per_90",
    "saves_per_90",
    "clean_sheets_per_90",
    "defensive_contribution",
    "defensive_contribution_per_90",
    "penalties_order",
    "direct_freekicks_order",
    "corners_and_indirect_freekicks_order",
)


def _coerce_player_field(name, raw_value, default):
    if raw_value is None:
        return default
    if isinstance(default, float):
        return float(raw_value)
    return raw_value


def ingest_players(bootstrap_static):
    """Upsert every player from bootstrap-static's `elements`. Player pool
    membership/price/status is cheap and always refreshed in full; the
    expensive per-player history is fetched separately (see
    ingest_player_summary) with staleness tracking."""
    elements = bootstrap_static.get("elements", [])
    player_ids = []
    for e in elements:
        field_model = Player._meta.get_field
        defaults = {}
        for name in _PLAYER_DEFAULT_FIELDS:
            default = field_model(name).get_default()
            defaults[name] = _coerce_player_field(name, e.get(name), default)

        Player.objects.update_or_create(
            id=e["id"],
            defaults={"team_id": e["team"], **defaults},
        )
        player_ids.append(e["id"])
    return player_ids


def stale_player_ids(player_ids=None):
    """Player ids whose element-summary is missing or older than
    FPL_GLOBAL_DATA_FRESHNESS_SECONDS, restricted to player_ids if given."""
    qs = Player.objects.all()
    if player_ids is not None:
        qs = qs.filter(id__in=player_ids)
    return [
        p.id
        for p in qs.only("id", "summary_fetched_at")
        if _is_stale(p.summary_fetched_at)
    ]


def unavailable_player_ids(player_ids=None):
    """Player ids whose availability multiplier is zero - see
    "Unavailable Players Skipped During Bulk Ingestion"."""
    qs = Player.objects.all()
    if player_ids is not None:
        qs = qs.filter(id__in=player_ids)
    return [
        p.id
        for p in qs.only("id", "status", "chance_of_playing_next_round")
        if availability_multiplier(p.status, p.chance_of_playing_next_round) == 0.0
    ]


def ingest_player_summary(player_id):
    """
    Fetch and persist one player's element-summary: current-season
    gameweek history and past-season history. Committed as a single
    player's worth of work so an interrupted ingestion run leaves
    previously-ingested players intact (see "Ingestion Is Interruptible
    and Resumable").
    """
    summary = client.get_element_summary(player_id)

    for gw in summary.get("history", []):
        PlayerGameweekHistory.objects.update_or_create(
            player_id=player_id,
            round=gw["round"],
            defaults={
                "opponent_team_id": gw.get("opponent_team"),
                "was_home": bool(gw.get("was_home", False)),
                "kickoff_time": gw.get("kickoff_time"),
                "total_points": gw.get("total_points", 0),
                "minutes": gw.get("minutes", 0),
                "goals_scored": gw.get("goals_scored", 0),
                "assists": gw.get("assists", 0),
                "clean_sheets": gw.get("clean_sheets", 0),
                "goals_conceded": gw.get("goals_conceded", 0),
                "own_goals": gw.get("own_goals", 0),
                "penalties_saved": gw.get("penalties_saved", 0),
                "penalties_missed": gw.get("penalties_missed", 0),
                "yellow_cards": gw.get("yellow_cards", 0),
                "red_cards": gw.get("red_cards", 0),
                "saves": gw.get("saves", 0),
                "bonus": gw.get("bonus", 0),
                "influence": float(gw.get("influence") or 0),
                "creativity": float(gw.get("creativity") or 0),
                "threat": float(gw.get("threat") or 0),
                "ict_index": float(gw.get("ict_index") or 0),
                "expected_goals": float(gw.get("expected_goals") or 0),
                "expected_assists": float(gw.get("expected_assists") or 0),
                "expected_goal_involvements": float(
                    gw.get("expected_goal_involvements") or 0
                ),
                "expected_goals_conceded": float(
                    gw.get("expected_goals_conceded") or 0
                ),
                "defensive_contribution": float(gw.get("defensive_contribution") or 0),
            },
        )

    for season in summary.get("history_past", []):
        PlayerSeasonHistory.objects.update_or_create(
            player_id=player_id,
            season_name=season["season_name"],
            defaults={
                "start_cost": season.get("start_cost", 0),
                "end_cost": season.get("end_cost", 0),
                "total_points": season.get("total_points", 0),
                "minutes": season.get("minutes", 0),
                "goals_scored": season.get("goals_scored", 0),
                "assists": season.get("assists", 0),
                "clean_sheets": season.get("clean_sheets", 0),
                "goals_conceded": season.get("goals_conceded", 0),
                "own_goals": season.get("own_goals", 0),
                "penalties_saved": season.get("penalties_saved", 0),
                "penalties_missed": season.get("penalties_missed", 0),
                "yellow_cards": season.get("yellow_cards", 0),
                "red_cards": season.get("red_cards", 0),
                "saves": season.get("saves", 0),
                "bonus": season.get("bonus", 0),
                "influence": float(season.get("influence") or 0),
                "creativity": float(season.get("creativity") or 0),
                "threat": float(season.get("threat") or 0),
                "ict_index": float(season.get("ict_index") or 0),
                "expected_goals": float(season.get("expected_goals") or 0),
                "expected_assists": float(season.get("expected_assists") or 0),
                "expected_goal_involvements": float(
                    season.get("expected_goal_involvements") or 0
                ),
                "expected_goals_conceded": float(
                    season.get("expected_goals_conceded") or 0
                ),
                "defensive_contribution_per_90": float(
                    season.get("defensive_contribution_per_90") or 0
                ),
            },
        )

    Player.objects.filter(id=player_id).update(summary_fetched_at=timezone.now())


def ingest_fixtures():
    fixtures_payload = client.get_fixtures()
    for f in fixtures_payload:
        Fixture.objects.update_or_create(
            id=f["id"],
            defaults={
                "event": f.get("event"),
                "team_h_id": f["team_h"],
                "team_a_id": f["team_a"],
                "team_h_score": f.get("team_h_score"),
                "team_a_score": f.get("team_a_score"),
                "team_h_difficulty": f.get("team_h_difficulty"),
                "team_a_difficulty": f.get("team_a_difficulty"),
                "kickoff_time": f.get("kickoff_time"),
                "finished": bool(f.get("finished", False)),
            },
        )


def run_full_ingestion(force_refresh=False):
    """
    Orchestrates a full ingestion pass: bootstrap-static (teams + player
    pool), fixtures, then per-player summaries for whichever players are
    stale (or all of them, if force_refresh) and available (see
    "Staleness-Aware Incremental Refresh" and "Unavailable Players Skipped
    During Bulk Ingestion").

    Returns a dict summary suitable for a management command to report.
    """
    bootstrap_static = client.get_bootstrap_static()
    season_start_year = derive_current_season_start_year(bootstrap_static)

    ingest_teams(bootstrap_static)
    player_ids = ingest_players(bootstrap_static)
    ingest_fixtures()

    skip_ids = set(unavailable_player_ids(player_ids))
    if force_refresh:
        to_fetch = [pid for pid in player_ids if pid not in skip_ids]
    else:
        stale_ids = set(stale_player_ids(player_ids))
        to_fetch = [pid for pid in player_ids if pid in stale_ids and pid not in skip_ids]

    fetched = 0
    failed = []
    for player_id in to_fetch:
        try:
            ingest_player_summary(player_id)
            fetched += 1
        except Exception:
            logger.exception("Failed to ingest element-summary for player %s", player_id)
            failed.append(player_id)

    return {
        "season_start_year": season_start_year,
        "teams": len(bootstrap_static.get("teams", [])),
        "players_total": len(player_ids),
        "players_skipped_unavailable": len(skip_ids),
        "players_fetched": fetched,
        "players_failed": failed,
    }
