"""
Fixture difficulty - fixture-difficulty-rating's existing requirements
(team strength field resolution, fallback to overall strength, neutral 1.0
when no data) ported from fpl-buddy/controllers/player.py's
fixture_difficulty/_resolve_team_strength, plus this change's additions:
a configurable horizon and double/blank gameweek detection.

Two concerns are kept deliberately separate, in different functions:

- `mean_fixture_difficulty` answers "how hard are the opponents" as a
  ratio comparable *across* horizons (a mean, not a sum) - this is what
  makes "GW1-3 is easier than GW1-8" a meaningful statement.
- fixture *count* within the horizon (double/blank gameweeks) is exposed
  separately via `detect_double_gameweeks`/`detect_blank_gameweeks` and
  `fixture_count_factor`. The scoring engine (scoring.performance)
  combines the two into one fixture component, so "plays more fixtures
  this horizon" is rewarded without corrupting the difficulty ratio's
  cross-horizon comparability.
"""

from django.db.models import Min, Q

from ..models import Fixture, Player


def resolve_team_strength(team, attr_name, home_or_away):
    """
    Position-specific strength (e.g. strength_attack_home), falling back to
    strength_overall_{home_or_away} when the position-specific value is
    0/unset, else None (signalling "no data" up to the caller, which
    applies the neutral 1.0 fallback) - see "Fallback for Unavailable
    Position-Specific Strength".
    """
    position_specific = getattr(team, f"{attr_name}_{home_or_away}", 0)
    if position_specific:
        return position_specific

    overall = getattr(team, f"strength_overall_{home_or_away}", 0)
    if overall:
        return overall

    return None


def fixture_strength_ratio(position, player_team, opponent_team, is_home):
    """
    One fixture's difficulty ratio for a player of the given position:
    their team's relevant strength (attack for forwards, defence for
    everyone else) divided by the opponent's complementary strength,
    resolved for home/away context. Neutral 1.0 when either side's
    strength data is unavailable - see "No strength data available at
    all".
    """
    player_home_or_away = "home" if is_home else "away"
    opponent_home_or_away = "away" if is_home else "home"

    is_forward = position == Player.Position.FORWARD
    player_attr = "strength_attack" if is_forward else "strength_defence"
    opponent_attr = "strength_defence" if is_forward else "strength_attack"

    player_strength = resolve_team_strength(player_team, player_attr, player_home_or_away)
    opponent_strength = resolve_team_strength(
        opponent_team, opponent_attr, opponent_home_or_away
    )

    if not player_strength or not opponent_strength:
        return 1.0

    return player_strength / opponent_strength


def current_season_start_year_from_fixtures():
    """
    Same month-cutoff logic as fpl_data.ingestion.
    derive_current_season_start_year, but sourced from already-ingested
    Fixture rows rather than a fresh bootstrap-static call - lets
    score_players/build_squads determine the season without hitting the
    FPL API (see squad-suggestion-api's "Scoring runs without
    re-ingesting").
    """
    earliest = (
        Fixture.objects.exclude(kickoff_time__isnull=True).order_by("kickoff_time").first()
    )
    if earliest is None:
        raise ValueError("No fixtures ingested yet - run ingest_fpl_data first")
    kt = earliest.kickoff_time
    return kt.year if kt.month >= 7 else kt.year - 1


def next_event_number():
    """The earliest gameweek with at least one unfinished fixture - the
    "next_event" that scoring projects fixture difficulty from. Returns 1
    when nothing is marked finished yet (preseason: no fixtures played)."""
    result = Fixture.objects.filter(finished=False).exclude(event__isnull=True).aggregate(
        Min("event")
    )
    return result["event__min"] or 1


def upcoming_team_fixtures(team_id, from_event, horizon):
    """Fixture rows for team_id within [from_event, from_event + horizon),
    ordered by event, home and away, including every fixture of a double
    gameweek - see "Every fixture in a double gameweek contributes"."""
    return (
        Fixture.objects.filter(
            Q(team_h_id=team_id) | Q(team_a_id=team_id),
            event__gte=from_event,
            event__lt=from_event + horizon,
        )
        .select_related("team_h", "team_a")
        .order_by("event")
    )


def mean_fixture_difficulty(player, from_event, horizon):
    """
    Returns (mean_ratio, fixture_count) for player's team across
    [from_event, from_event + horizon). mean_ratio is the neutral 1.0 and
    fixture_count is 0 when the team has no fixtures scheduled in that
    window - see "No upcoming fixtures" and "Horizon extending past the
    final gameweek" (the query above naturally returns fewer rows without
    erroring when the horizon runs past the last scheduled fixture).
    """
    fixtures = list(upcoming_team_fixtures(player.team_id, from_event, horizon))
    if not fixtures:
        return 1.0, 0

    ratios = []
    for fx in fixtures:
        is_home = fx.team_h_id == player.team_id
        opponent = fx.team_a if is_home else fx.team_h
        ratios.append(
            fixture_strength_ratio(player.element_type, player.team, opponent, is_home)
        )
    return sum(ratios) / len(ratios), len(fixtures)


def fixture_count_factor(fixture_count, horizon):
    """
    fixture_count / horizon: 1.0 for a normal one-fixture-per-gameweek
    run, above 1.0 when double gameweeks push the count up, and 0.0 for a
    fully blank team - the multiplier scoring.performance applies to
    mean_fixture_difficulty so more fixtures are rated more favourably
    without changing the difficulty ratio itself.
    """
    if horizon <= 0:
        return 0.0
    return fixture_count / horizon


def detect_double_gameweeks(team_id, from_event, horizon):
    """{event: fixture_count} for events in the horizon window where the
    team has more than one fixture."""
    counts = {}
    for fx in upcoming_team_fixtures(team_id, from_event, horizon):
        if fx.event is None:
            continue
        counts[fx.event] = counts.get(fx.event, 0) + 1
    return {event: count for event, count in counts.items() if count > 1}


def detect_blank_gameweeks(team_id, from_event, horizon):
    """
    Events within the horizon window where at least one other team plays
    but this team has no fixture.
    """
    events_with_any_fixture = set(
        Fixture.objects.filter(
            event__gte=from_event, event__lt=from_event + horizon
        ).values_list("event", flat=True)
    )
    events_with_any_fixture.discard(None)

    team_events = {
        fx.event
        for fx in upcoming_team_fixtures(team_id, from_event, horizon)
        if fx.event is not None
    }

    return events_with_any_fixture - team_events
