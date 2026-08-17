"""
ILP-based squad selection - squad-optimization's "Squad Selection Satisfies
FPL Constraints by Construction". Uses pulp rather than the prototype's
greedy remove-and-retry loop (fpl-buddy/main.py's select_team) - see
suggest-best-squad design.md Decision 1: constraints are declared once and
enforced simultaneously, so every produced squad is legal by construction
and infeasibility is reported rather than silently looped over.
"""

from collections import defaultdict

import pulp

from .models import Player

BUDGET_TENTHS = 1000  # FPL's £100.0m budget, in tenths of a million
POSITION_QUOTAS = {
    Player.Position.GOALKEEPER: 2,
    Player.Position.DEFENDER: 5,
    Player.Position.MIDFIELDER: 5,
    Player.Position.FORWARD: 3,
}
MAX_PER_CLUB = 3
SQUAD_SIZE = 15


class InfeasibleStrategyError(Exception):
    """See "Infeasible Selection Is Reported, Not Approximated" - raised
    instead of returning a partial, over-budget, or rule-violating squad."""

    def __init__(self, strategy_name):
        self.strategy_name = strategy_name
        super().__init__(f"Strategy {strategy_name!r} has no feasible squad")


def select_squad(scored_players, strategy):
    """
    scored_players: iterable of fpl_data.scoring.engine.ScoredPlayer.
    Returns the 15 selected ScoredPlayer, maximizing total total_score
    subject to budget, exact position quotas, the 3-per-club limit, and
    the strategy's own hard constraints. Excludes zero-availability
    players from the eligible pool entirely - see "Unavailable Players
    Excluded from Selection".
    """
    eligible = [
        sp for sp in scored_players if sp.components.availability_multiplier > 0
    ]
    if not eligible:
        raise InfeasibleStrategyError(strategy.name)

    prob = pulp.LpProblem(f"squad_{strategy.name}", pulp.LpMaximize)
    x = {
        sp.player.id: pulp.LpVariable(f"x_{sp.player.id}", cat="Binary")
        for sp in eligible
    }

    prob += pulp.lpSum(x[sp.player.id] * sp.total_score for sp in eligible)

    prob += (
        pulp.lpSum(x[sp.player.id] * sp.player.now_cost for sp in eligible)
        <= BUDGET_TENTHS
    )
    prob += pulp.lpSum(x.values()) == SQUAD_SIZE

    for position, quota in POSITION_QUOTAS.items():
        prob += (
            pulp.lpSum(
                x[sp.player.id] for sp in eligible if sp.player.element_type == position
            )
            == quota
        )

    club_ids = {sp.player.team_id for sp in eligible}
    for club_id in club_ids:
        prob += (
            pulp.lpSum(x[sp.player.id] for sp in eligible if sp.player.team_id == club_id)
            <= MAX_PER_CLUB
        )

    for constraint in strategy.hard_constraints:
        if constraint.type == "min_count_above_price":
            prob += (
                pulp.lpSum(
                    x[sp.player.id]
                    for sp in eligible
                    if sp.player.now_cost > constraint.price_tenths
                )
                >= constraint.count
            )
        else:
            raise ValueError(f"Unknown hard constraint type {constraint.type!r}")

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))

    if pulp.LpStatus[status] != "Optimal":
        raise InfeasibleStrategyError(strategy.name)

    selected_ids = {pid for pid, var in x.items() if (var.value() or 0) >= 0.5}
    return [sp for sp in eligible if sp.player.id in selected_ids]


def shortlist_by_position(scored_players, top_n=15):
    """Ranked (descending total_score) players per position, independent
    of the ILP solve - see "Shortlist of Viable Options per Position"."""
    by_position = defaultdict(list)
    for sp in scored_players:
        by_position[sp.player.element_type].append(sp)

    return {
        position: sorted(players, key=lambda sp: sp.total_score, reverse=True)[:top_n]
        for position, players in by_position.items()
    }
