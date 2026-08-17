"""
Historical replay of the scoring/selection pipeline - see
squad-backtesting. Replays a past gameweek using only data that would
have been available at that point, measures the selected squad's realized
points against three baselines, and supports zeroing one scoring factor
at a time (ablation) to measure its contribution.
"""

import random
from dataclasses import dataclass, replace

from django.db.models import Sum

from .models import Player, PlayerGameweekHistory
from .optimization import InfeasibleStrategyError, select_squad
from .scoring.engine import ScoredPlayer, combine_score
from .scoring.performance import compute_player_components
from .selection import select_captains, select_formation_and_starters
from .strategies import OBJECTIVE_TERMS, StrategyConfig, get_strategy


@dataclass
class BacktestResult:
    replay_event: int
    strategy_name: str
    squad_realized_points: float
    baseline_realized_points: dict
    captain_outscored_median: dict


def score_pool_as_of(strategy: StrategyConfig, season_start_year, replay_event):
    """
    Like scoring.engine.score_player_pool, but restricted to gameweek
    history strictly before `replay_event` - see "Backtest Uses Only Data
    Available at the Replayed Gameweek". Fixture difficulty is naturally
    point-in-time correct too: it's computed from Fixture.event, not from
    Fixture.finished (which reflects the current DB state, not the state
    as of the replay), so passing replay_event as next_event already
    excludes anything before it.
    """
    players = Player.objects.select_related("team").prefetch_related(
        "gameweek_history", "season_history"
    )
    for player in players:
        gw_histories = [
            gw for gw in player.gameweek_history.all() if gw.round < replay_event
        ]
        components = compute_player_components(
            player,
            gameweek_histories=gw_histories,
            season_histories=list(player.season_history.all()),
            next_event=replay_event,
            horizon=strategy.horizon,
            current_season_start_year=season_start_year,
        )
        total_score = combine_score(components, strategy)
        yield ScoredPlayer(player=player, components=components, total_score=total_score)


def realized_points(player_ids, from_event, horizon):
    """Sum of actual FPL points the given players scored across
    [from_event, from_event + horizon) - see "Backtest Measures Realized
    Points Over the Horizon"."""
    if not player_ids:
        return 0
    total = PlayerGameweekHistory.objects.filter(
        player_id__in=player_ids, round__gte=from_event, round__lt=from_event + horizon
    ).aggregate(total=Sum("total_points"))["total"]
    return total or 0


def _bare_strategy(horizon, name="baseline"):
    """A strategy carrying no weights and no hard constraints, used to
    build baseline squads under only the universal FPL rules select_squad
    always enforces - see "Baselines obey the same constraints"."""
    return StrategyConfig(name=name, horizon=horizon, weights={})


def random_legal_squad(scored_players, strategy, rng=None):
    rng = rng or random.Random()
    randomized = [
        ScoredPlayer(player=sp.player, components=sp.components, total_score=rng.random())
        for sp in scored_players
    ]
    return select_squad(randomized, _bare_strategy(strategy.horizon, "random_baseline"))


def highest_ownership_squad(scored_players, strategy):
    ownership_weighted = [
        ScoredPlayer(
            player=sp.player,
            components=sp.components,
            total_score=sp.components.ownership_component,
        )
        for sp in scored_players
    ]
    return select_squad(
        ownership_weighted, _bare_strategy(strategy.horizon, "template_baseline")
    )


def price_only_squad(scored_players, strategy):
    """
    A squad chosen purely by "expensive = good" - maximizes total price
    spent under the budget, using no performance data at all. See
    "Backtest Compares Against Baselines".
    """
    price_weighted = [
        ScoredPlayer(
            player=sp.player, components=sp.components, total_score=float(sp.player.now_cost)
        )
        for sp in scored_players
    ]
    return select_squad(
        price_weighted, _bare_strategy(strategy.horizon, "price_only_baseline")
    )


def ablatable_factors():
    """See "Every weighted factor can be ablated"."""
    return list(OBJECTIVE_TERMS)


def ablate(strategy: StrategyConfig, factor: str) -> StrategyConfig:
    """A copy of `strategy` with exactly one objective coefficient zeroed
    - see "Per-Factor Ablation" and "Ablating one factor leaves others
    unchanged"."""
    if factor not in OBJECTIVE_TERMS:
        raise ValueError(f"{factor!r} is not an ablatable factor: {OBJECTIVE_TERMS}")
    new_weights = dict(strategy.weights)
    new_weights[factor] = 0.0
    return replace(strategy, name=f"{strategy.name}__ablate_{factor}", weights=new_weights)


def captain_outscored_median(starter_ids, captain_id, event):
    """Did `captain_id` outscore the median starter's actual points in
    gameweek `event`? See "Captaincy Measured Independently of Squad
    Quality"."""
    points_by_player = dict(
        PlayerGameweekHistory.objects.filter(
            player_id__in=starter_ids, round=event
        ).values_list("player_id", "total_points")
    )
    scores = sorted(points_by_player.get(pid, 0) for pid in starter_ids)
    n = len(scores)
    if n == 0:
        return False
    median = scores[n // 2] if n % 2 == 1 else (scores[n // 2 - 1] + scores[n // 2]) / 2
    return points_by_player.get(captain_id, 0) > median


def run_backtest(strategy_name, replay_events, season_start_year, ablate_factor=None):
    """
    Runs the full pipeline (score -> select -> starting XI -> captains)
    for each gameweek in `replay_events`, using only data available at
    that point, and returns one BacktestResult per replay point that
    produced a feasible squad (infeasible replay points are skipped, not
    reported as a zero result - see squad-optimization's "Infeasible
    Selection Is Reported, Not Approximated").
    """
    strategy = get_strategy(strategy_name)
    if ablate_factor is not None:
        strategy = ablate(strategy, ablate_factor)

    results = []
    for replay_event in replay_events:
        scored = list(score_pool_as_of(strategy, season_start_year, replay_event))

        try:
            squad = select_squad(scored, strategy)
        except InfeasibleStrategyError:
            continue

        squad_ids = [sp.player.id for sp in squad]
        squad_points = realized_points(squad_ids, replay_event, strategy.horizon)

        baselines = {
            "random": realized_points(
                [sp.player.id for sp in random_legal_squad(scored, strategy)],
                replay_event,
                strategy.horizon,
            ),
            "template": realized_points(
                [sp.player.id for sp in highest_ownership_squad(scored, strategy)],
                replay_event,
                strategy.horizon,
            ),
            "price_only": realized_points(
                [sp.player.id for sp in price_only_squad(scored, strategy)],
                replay_event,
                strategy.horizon,
            ),
        }

        xi = select_formation_and_starters(squad)
        captains = select_captains(xi.starters)
        starter_ids = [sp.player.id for sp in xi.starters]

        captain_accuracy = {
            "season": captain_outscored_median(
                starter_ids, captains.season.captain.player.id, replay_event
            ),
            "next_gw": captain_outscored_median(
                starter_ids, captains.next_gw.captain.player.id, replay_event
            ),
            "differential": (
                captain_outscored_median(
                    starter_ids, captains.differential.captain.player.id, replay_event
                )
                if captains.differential
                else None
            ),
        }

        results.append(
            BacktestResult(
                replay_event=replay_event,
                strategy_name=strategy.name,
                squad_realized_points=squad_points,
                baseline_realized_points=baselines,
                captain_outscored_median=captain_accuracy,
            )
        )

    return results
