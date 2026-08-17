"""
Player performance scoring - player-performance-scoring's requirements,
both the pre-existing ones (per-gameweek score, defensive contribution
weighting, season aggregate weighting applied once) ported from
fpl-buddy/main.py's calculate_performance, and this change's additions
(realized/expected separation + regression signal, set-piece duty,
ownership, rotation risk, explicit preseason handling, absent-history
flag).

Architecture note: unlike the prototype's calculate_performance, which
folds everything into one hardcoded multiplicative `combined_score`, this
module returns independent components (see PlayerScoreComponents). The
strategy's weight vector (fpl_data.strategies) combines them into a final
score - see squad-optimization's "Strategies Are Named Weight Vectors".
Keeping components additive and separately inspectable is what makes
per-factor ablation (squad-backtesting) possible, and it's why the
ownership component is stored as ownership.selected_by_percent's raw value
rather than pre-multiplied by a fixed sign.
"""

from dataclasses import dataclass

from ..models import Player
from .availability import availability_multiplier, discipline_factor
from .fixtures import fixture_count_factor, mean_fixture_difficulty

# See "Season Aggregate Weighting Applied Once": the accumulated weighted
# past-season score is divided by this constant exactly once, not once per
# season - matches fpl-buddy/main.py's DECAY_FACTOR/38 normalization.
PAST_SEASON_DECAY_FACTOR = 0.5
PAST_SEASON_NORMALIZATION_DIVISOR = 38.0
DEFENSIVE_CONTRIBUTION_SEASON_WEIGHT = 0.1

# How much past-season signal to blend in once current-season data exists.
# When there is no current-season data at all, past history is the entire
# signal (weight 1.0) - see "Scoring Degrades Explicitly Without
# Current-Season History".
PAST_HISTORY_BLEND_WEIGHT = 0.3

# See "Set-Piece Duty Contributes to Scoring": a more senior order
# contributes more; an order beyond 3rd choice or a null order contributes
# nothing.
SETPIECE_ORDER_VALUES = {1: 1.0, 2: 0.5, 3: 0.25}


@dataclass
class PlayerScoreComponents:
    expected_component: float
    realized_component: float
    regression_signal: float
    fixture_component: float
    setpiece_component: float
    ownership_component: float
    rotation_component: float
    availability_multiplier: float
    discipline_multiplier: float
    has_history: bool
    next_gw_score: float


def gameweek_shaped_score(ict_index, expected_goal_involvements, expected_goals_conceded,
                           defensive_contribution, position, is_goalkeeper_defensive_exempt=True):
    """
    The corrected per-gameweek performance score - see MODIFIED
    "Per-Gameweek Performance Score". The involvement term counts
    expected_goal_involvements once (the prototype additionally summed
    expected_goals + expected_assists alongside it, double-counting since
    FPL defines expected_goal_involvements as their sum).
    """
    involvement = expected_goal_involvements
    if position != Player.Position.FORWARD:
        involvement -= expected_goals_conceded

    defensive_term = 0.0
    if not (is_goalkeeper_defensive_exempt and position == Player.Position.GOALKEEPER):
        defensive_term = defensive_contribution / 2

    return max(0.0, ict_index + involvement + defensive_term)


def past_season_shaped_score(season_history, position):
    """
    Same shape as gameweek_shaped_score but for a PlayerSeasonHistory row,
    which has no per-match `defensive_contribution` field (only the
    per-90 rate, added separately in past_history_score at a different
    weight - see the docstring there).
    """
    involvement = season_history.expected_goal_involvements
    if position != Player.Position.FORWARD:
        involvement -= season_history.expected_goals_conceded
    return max(0.0, season_history.ict_index + involvement)


def past_history_score(season_histories, position, current_season_start_year):
    """
    Weighted aggregate of a player's past-season history: more recent
    seasons and seasons with more minutes played count for more. Ported
    from fpl-buddy/main.py's calculate_performance past-season loop.

    Divided by PAST_SEASON_NORMALIZATION_DIVISOR exactly once, after the
    loop - see "Season Aggregate Weighting Applied Once".
    """
    if not season_histories:
        return 0.0

    total_seasons = len(season_histories)
    max_minutes = max((s.minutes for s in season_histories), default=0) or 1

    accumulated = 0.0
    for season in season_histories:
        season_year = int(season.season_name.split("/")[0])
        recency_weight = 1 + (current_season_start_year - season_year) / total_seasons
        minutes_weight = 1 + (season.minutes / max_minutes) if season.minutes > 0 else 1
        weight = recency_weight * minutes_weight * PAST_SEASON_DECAY_FACTOR

        shaped = past_season_shaped_score(season, position)
        defensive_bonus = (
            season.defensive_contribution_per_90 * DEFENSIVE_CONTRIBUTION_SEASON_WEIGHT
        )
        accumulated += shaped * weight + defensive_bonus

    return accumulated / PAST_SEASON_NORMALIZATION_DIVISOR


def setpiece_component(player):
    """See "Set-Piece Duty Contributes to Scoring": sum of each set-piece
    type's order-based value, 0 for an unlisted order."""
    total = 0.0
    for order in (
        player.penalties_order,
        player.direct_freekicks_order,
        player.corners_and_indirect_freekicks_order,
    ):
        if order is not None:
            total += SETPIECE_ORDER_VALUES.get(order, 0.0)
    return total


def rotation_component(player, played_gameweeks):
    """
    See "Rotation Risk Contributes to Scoring": starts_per_90 discounted
    by how variable recent minutes have been (a consistent 90-minute
    starter has near-zero variance; a rotation risk alternates between
    full matches and unused appearances). Distinct from the availability
    multiplier, which reflects current injury/suspension status only, not
    a manager's rotation pattern.
    """
    if len(played_gameweeks) < 2:
        return player.starts_per_90

    minutes = [gw.minutes for gw in played_gameweeks]
    mean_minutes = sum(minutes) / len(minutes)
    variance = sum((m - mean_minutes) ** 2 for m in minutes) / len(minutes)
    stddev = variance**0.5
    # A full match is ~90 minutes; normalize variability against that so
    # "consistency" lands in roughly [0, 1].
    consistency = max(0.0, 1 - min(1.0, stddev / 90))
    return player.starts_per_90 * consistency


def compute_player_components(
    player,
    gameweek_histories,
    season_histories,
    next_event,
    horizon,
    current_season_start_year,
):
    """
    The full set of independent scoring components for one player. See the
    module docstring for why these stay separate rather than being
    combined here - combination is the strategy's job.
    """
    played_gws = [gw for gw in gameweek_histories if gw.minutes > 0]
    num_gws = len(played_gws)
    has_current_season_data = num_gws > 0
    has_history = has_current_season_data or bool(season_histories)

    past_score = past_history_score(
        season_histories, player.element_type, current_season_start_year
    )

    if has_current_season_data:
        current_mean = sum(
            gameweek_shaped_score(
                gw.ict_index,
                gw.expected_goal_involvements,
                gw.expected_goals_conceded,
                gw.defensive_contribution,
                player.element_type,
            )
            for gw in played_gws
        ) / num_gws
        # Blend in past-season signal once current-season data exists,
        # rather than letting it dominate as it does preseason.
        expected = current_mean + PAST_HISTORY_BLEND_WEIGHT * past_score
        realized = float(player.total_points)
        regression = float(player.goals_scored) - float(player.expected_goals)
    else:
        # No current-season gameweeks played yet (preseason, or a new
        # signing/promoted-club player who hasn't featured) - see
        # "Scoring Degrades Explicitly Without Current-Season History".
        # Past history carries the entire expected-output signal instead
        # of dividing by a gameweek count of zero. realized/regression
        # have nothing to report yet this season.
        expected = past_score
        realized = 0.0
        regression = 0.0

    mean_ratio, fixture_count = mean_fixture_difficulty(player, next_event, horizon)
    fixture = mean_ratio * fixture_count_factor(fixture_count, horizon)

    next_gw_ratio, next_gw_fixture_count = mean_fixture_difficulty(player, next_event, 1)
    if next_gw_fixture_count == 0:
        next_gw_score = 0.0
    else:
        next_gw_score = expected / (1 + next_gw_ratio)

    return PlayerScoreComponents(
        expected_component=expected,
        realized_component=realized,
        regression_signal=regression,
        fixture_component=fixture,
        setpiece_component=setpiece_component(player),
        ownership_component=player.selected_by_percent,
        rotation_component=rotation_component(player, played_gws),
        availability_multiplier=availability_multiplier(
            player.status, player.chance_of_playing_next_round
        ),
        discipline_multiplier=discipline_factor(player.red_cards),
        has_history=has_history,
        next_gw_score=next_gw_score,
    )
