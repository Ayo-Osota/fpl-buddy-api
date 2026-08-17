"""
Availability and discipline multipliers - player-performance-scoring's
"Player Availability from Status and Fitness" and "Discipline Risk from
Season Red Cards" (both pre-existing, unmodified requirements; ported
1:1 from the prototype's status_multiplier/normalize_fitness in
fpl-buddy/controllers/player.py and the discipline factor in main.py).

This is the single canonical implementation - fpl_data.ingestion imports
availability_multiplier from here rather than reimplementing it, so the
"skip unavailable players during ingestion" rule and the "availability is
a multiplier, not a weighted score term" rule can never drift apart.
"""

UNAVAILABLE_STATUSES = {"i", "s", "u"}

DISCIPLINE_FACTOR_FLOOR = 0.85
DISCIPLINE_FACTOR_PER_RED_CARD = 0.05


def status_multiplier(status):
    if status in UNAVAILABLE_STATUSES:
        return 0.0
    return 1.0


def fitness_ratio(chance_of_playing_next_round):
    if chance_of_playing_next_round is None:
        return 1.0
    return chance_of_playing_next_round / 100


def availability_multiplier(status, chance_of_playing_next_round):
    """min(fitness ratio, status multiplier) - a suspended/injured/
    unavailable player is 0 regardless of fitness percentage; an available
    or doubtful player defers to their fitness ratio."""
    return min(fitness_ratio(chance_of_playing_next_round), status_multiplier(status))


def discipline_factor(red_cards_this_season):
    """
    max(0.85, 1 - 0.05 * red_cards). FPL's own -3 point penalty is already
    inside that gameweek's total_points, so this is a forward-looking
    suspension-risk discount, not a repeat of that penalty.
    """
    return max(
        DISCIPLINE_FACTOR_FLOOR,
        1 - DISCIPLINE_FACTOR_PER_RED_CARD * red_cards_this_season,
    )
