"""
Writes scoring and squad-selection results to the database - see
squad-suggestion-api's "Scoring Runs Are Persisted with Component Scores"
and "Squad Response Contains Full Composition".
"""

from .models import PlayerScore, ScoringRun, SuggestedSquad, SuggestedSquadPlayer
from .scoring.engine import ScoredPlayer
from .scoring.performance import PlayerScoreComponents
from .strategies import strategy_to_dict


def persist_scoring_run(strategy, season_start_year, scored_players):
    """Creates one ScoringRun and one PlayerScore per player, carrying
    every component alongside the final total_score."""
    run = ScoringRun.objects.create(
        strategy_name=strategy.name,
        weights=strategy_to_dict(strategy),
        season_start_year=season_start_year,
    )

    PlayerScore.objects.bulk_create(
        [
            PlayerScore(
                scoring_run=run,
                player=sp.player,
                total_score=sp.total_score,
                next_gw_score=sp.components.next_gw_score,
                expected_component=sp.components.expected_component,
                realized_component=sp.components.realized_component,
                regression_signal=sp.components.regression_signal,
                fixture_component=sp.components.fixture_component,
                setpiece_component=sp.components.setpiece_component,
                ownership_component=sp.components.ownership_component,
                rotation_component=sp.components.rotation_component,
                availability_multiplier=sp.components.availability_multiplier,
                discipline_multiplier=sp.components.discipline_multiplier,
                has_history=sp.components.has_history,
            )
            for sp in scored_players
        ]
    )
    return run


def scored_players_from_run(scoring_run):
    """
    Reconstructs ScoredPlayer objects from a stored ScoringRun's
    PlayerScore rows - the read-side counterpart of persist_scoring_run.
    Lets build_squads select a squad from already-computed scores without
    recomputing them - see squad-suggestion-api's "Squad building runs
    without re-scoring".
    """
    scores = PlayerScore.objects.filter(scoring_run=scoring_run).select_related(
        "player", "player__team"
    )
    return [
        ScoredPlayer(
            player=ps.player,
            components=PlayerScoreComponents(
                expected_component=ps.expected_component,
                realized_component=ps.realized_component,
                regression_signal=ps.regression_signal,
                fixture_component=ps.fixture_component,
                setpiece_component=ps.setpiece_component,
                ownership_component=ps.ownership_component,
                rotation_component=ps.rotation_component,
                availability_multiplier=ps.availability_multiplier,
                discipline_multiplier=ps.discipline_multiplier,
                has_history=ps.has_history,
                next_gw_score=ps.next_gw_score,
            ),
            total_score=ps.total_score,
        )
        for ps in scores
    ]


def persist_suggested_squad(scoring_run, strategy, xi_result, captains, total_price):
    """Creates one SuggestedSquad (with the full XI/bench/formation/captain
    composition - see "Squad Response Contains Full Composition") and its
    15 SuggestedSquadPlayer membership rows."""
    differential = captains.differential

    squad = SuggestedSquad.objects.create(
        scoring_run=scoring_run,
        strategy_name=strategy.name,
        formation=xi_result.formation,
        total_price=total_price,
        season_captain=captains.season.captain.player,
        season_vice_captain=captains.season.vice_captain.player,
        next_gw_captain=captains.next_gw.captain.player,
        next_gw_vice_captain=captains.next_gw.vice_captain.player,
        differential_captain=differential.captain.player if differential else None,
        differential_vice_captain=(
            differential.vice_captain.player if differential else None
        ),
    )

    rows = [
        SuggestedSquadPlayer(squad=squad, player=sp.player, is_starter=True)
        for sp in xi_result.starters
    ]
    rows.extend(
        SuggestedSquadPlayer(
            squad=squad, player=sp.player, is_starter=False, bench_rank=rank
        )
        for rank, sp in enumerate(xi_result.bench_outfield, start=1)
    )
    rows.append(
        SuggestedSquadPlayer(
            squad=squad,
            player=xi_result.bench_goalkeeper.player,
            is_starter=False,
            is_bench_goalkeeper=True,
        )
    )
    SuggestedSquadPlayer.objects.bulk_create(rows)

    return squad
