"""
Orchestrates scoring an entire player pool for one strategy: computes each
player's PlayerScoreComponents (fpl_data.scoring.performance), then
combines them with the strategy's weight vector and the
availability/discipline multipliers into a single total_score - see
squad-optimization's "Strategies Are Named Weight Vectors".
"""

from dataclasses import dataclass

from ..models import Player
from ..strategies import OBJECTIVE_TERMS, StrategyConfig
from .performance import PlayerScoreComponents, compute_player_components


@dataclass
class ScoredPlayer:
    player: Player
    components: PlayerScoreComponents
    total_score: float


def combine_score(components: PlayerScoreComponents, strategy: StrategyConfig) -> float:
    """
    total_score = availability x discipline x (weighted sum of the seven
    ablatable components). Availability/discipline are multipliers rather
    than weighted terms (see player-performance-scoring's "Player
    Availability from Status and Fitness") specifically so a suspended
    player scores zero regardless of how the objective is weighted.
    """
    weighted_sum = sum(
        strategy.weight(term) * getattr(components, term) for term in OBJECTIVE_TERMS
    )
    return (
        components.availability_multiplier
        * components.discipline_multiplier
        * weighted_sum
    )


def score_player_pool(strategy: StrategyConfig, season_start_year: int, next_event: int):
    """
    Yields a ScoredPlayer for every player in the pool, using prefetched
    gameweek/season history to avoid N+1 queries against the full ~700
    player pool.
    """
    players = Player.objects.select_related("team").prefetch_related(
        "gameweek_history", "season_history"
    )
    for player in players:
        components = compute_player_components(
            player,
            gameweek_histories=list(player.gameweek_history.all()),
            season_histories=list(player.season_history.all()),
            next_event=next_event,
            horizon=strategy.horizon,
            current_season_start_year=season_start_year,
        )
        total_score = combine_score(components, strategy)
        yield ScoredPlayer(player=player, components=components, total_score=total_score)
