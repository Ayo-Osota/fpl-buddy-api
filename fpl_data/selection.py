"""
Starting XI, bench ordering, and captain recommendations from a selected
15-man squad - see starting-xi-selection.
"""

from collections import defaultdict
from dataclasses import dataclass

from .models import Player

# The eight legal FPL formations as (defenders, midfielders, forwards) -
# always 1 GK + 10 outfield, DEF in [3,5], MID in [2,5], FWD in [1,3].
LEGAL_FORMATIONS = [
    (3, 4, 3),
    (3, 5, 2),
    (4, 3, 3),
    (4, 4, 2),
    (4, 5, 1),
    (5, 2, 3),
    (5, 3, 2),
    (5, 4, 1),
]

# A starter below this ownership percentage is eligible for the
# differential captain recommendation. A fixed constant rather than a
# per-strategy knob - see suggest-best-squad design.md, which keeps
# captaincy logic strategy-independent.
DIFFERENTIAL_OWNERSHIP_THRESHOLD = 10.0


@dataclass
class StartingXIResult:
    starters: list
    bench_outfield: list  # ranked, descending score
    bench_goalkeeper: object  # ScoredPlayer
    formation: str  # e.g. "3-4-3"


@dataclass
class CaptainRecommendation:
    captain: object  # ScoredPlayer
    vice_captain: object  # ScoredPlayer


@dataclass
class CaptainRecommendations:
    season: CaptainRecommendation
    next_gw: CaptainRecommendation
    differential: CaptainRecommendation | None


def select_formation_and_starters(squad):
    """
    squad: the 15 selected ScoredPlayer. Evaluates all eight legal
    formations and returns the highest-scoring one - see "Formation
    Chosen by Evaluating All Legal Formations". Requires at least 2
    goalkeepers and enough outfield players to fill the most demanding
    formation, which a legal 2/5/5/3 squad always satisfies.
    """
    by_position = defaultdict(list)
    for sp in squad:
        by_position[sp.player.element_type].append(sp)
    for players in by_position.values():
        players.sort(key=lambda sp: sp.total_score, reverse=True)

    goalkeepers = by_position[Player.Position.GOALKEEPER]
    defenders = by_position[Player.Position.DEFENDER]
    midfielders = by_position[Player.Position.MIDFIELDER]
    forwards = by_position[Player.Position.FORWARD]

    starting_gk = goalkeepers[0]
    bench_gk = goalkeepers[1]

    best = None
    for d, m, f in LEGAL_FORMATIONS:
        starters = [starting_gk] + defenders[:d] + midfielders[:m] + forwards[:f]
        total = sum(sp.total_score for sp in starters)
        bench = defenders[d:] + midfielders[m:] + forwards[f:]
        if best is None or total > best[0]:
            best = (total, starters, bench, f"{d}-{m}-{f}")

    _, starters, bench, formation = best
    bench_outfield = sorted(bench, key=lambda sp: sp.total_score, reverse=True)

    return StartingXIResult(
        starters=starters,
        bench_outfield=bench_outfield,
        bench_goalkeeper=bench_gk,
        formation=formation,
    )


def _top_two_by(candidates, key):
    ranked = sorted(candidates, key=key, reverse=True)
    return CaptainRecommendation(captain=ranked[0], vice_captain=ranked[1])


def select_captains(starters, ownership_threshold=DIFFERENTIAL_OWNERSHIP_THRESHOLD):
    """
    Three recommendations, each drawn only from `starters` (never the
    bench - see "Captains Are Drawn from Starters Only") and each with a
    distinct vice-captain: season (highest total_score), next-gameweek
    (highest next_gw_score), and differential (highest next_gw_score
    among starters below the ownership threshold). Differential is None
    when fewer than two starters qualify - see "No eligible differential
    captain".
    """
    season = _top_two_by(starters, key=lambda sp: sp.total_score)
    next_gw = _top_two_by(starters, key=lambda sp: sp.components.next_gw_score)

    differential_candidates = [
        sp for sp in starters if sp.components.ownership_component < ownership_threshold
    ]
    if len(differential_candidates) < 2:
        differential = None
    else:
        differential = _top_two_by(
            differential_candidates, key=lambda sp: sp.components.next_gw_score
        )

    return CaptainRecommendations(season=season, next_gw=next_gw, differential=differential)
