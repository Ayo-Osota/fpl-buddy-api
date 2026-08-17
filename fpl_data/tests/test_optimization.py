from django.test import SimpleTestCase

from fpl_data.models import Player
from fpl_data.optimization import (
    InfeasibleStrategyError,
    select_squad,
    shortlist_by_position,
)
from fpl_data.scoring.engine import ScoredPlayer
from fpl_data.scoring.performance import PlayerScoreComponents
from fpl_data.strategies import HardConstraint, StrategyConfig


def make_scored_player(
    id_,
    position,
    team_id,
    now_cost,
    total_score,
    ownership=10.0,
    availability=1.0,
):
    player = Player(
        id=id_,
        code=100000 + id_,
        first_name="Test",
        second_name=f"Player{id_}",
        web_name=f"Player{id_}",
        team_id=team_id,
        element_type=position,
        now_cost=now_cost,
        selected_by_percent=ownership,
    )
    components = PlayerScoreComponents(
        expected_component=total_score,
        realized_component=0.0,
        regression_signal=0.0,
        fixture_component=1.0,
        setpiece_component=0.0,
        ownership_component=ownership,
        rotation_component=1.0,
        availability_multiplier=availability,
        discipline_multiplier=1.0,
        has_history=True,
        next_gw_score=total_score,
    )
    return ScoredPlayer(player=player, components=components, total_score=total_score)


NEUTRAL_STRATEGY = StrategyConfig(
    name="test_neutral",
    horizon=5,
    weights={"expected_component": 1.0},
)


_POSITION_CLUB_OFFSETS = {
    Player.Position.GOALKEEPER: 0,
    Player.Position.DEFENDER: 2,
    Player.Position.MIDFIELDER: 4,
    Player.Position.FORWARD: 6,
}


def build_pool(n_per_position=8, n_clubs=8, cheap=40, expensive=140):
    """
    A generous pool: enough players per position, spread over enough
    clubs, with varied price/score so budget, quota, and club constraints
    are all actually load-bearing (not vacuously satisfied).

    Each position's club assignment is offset from the others so the
    cheapest candidate in every position doesn't land on the *same* club -
    without the offset, all four positions' cheapest options collide on
    one club, making budget and the 3-per-club cap jointly infeasible
    for reasons that are an artifact of the fixture, not of select_squad.
    """
    pool = []
    pid = 1
    for position, offset in _POSITION_CLUB_OFFSETS.items():
        for i in range(n_per_position):
            team_id = ((i + offset) % n_clubs) + 1
            price = cheap + (expensive - cheap) * (i / max(n_per_position - 1, 1))
            score = 5.0 + i  # higher index -> higher score, to force real tradeoffs
            pool.append(
                make_scored_player(pid, position, team_id, int(price), score)
            )
            pid += 1
    return pool


class SelectSquadConstraintTests(SimpleTestCase):
    def test_budget_respected(self):
        squad = select_squad(build_pool(), NEUTRAL_STRATEGY)
        total_price = sum(sp.player.now_cost for sp in squad)
        self.assertLessEqual(total_price, 1000)

    def test_position_quotas_exact(self):
        squad = select_squad(build_pool(), NEUTRAL_STRATEGY)
        counts = {}
        for sp in squad:
            counts[sp.player.element_type] = counts.get(sp.player.element_type, 0) + 1
        self.assertEqual(counts[Player.Position.GOALKEEPER], 2)
        self.assertEqual(counts[Player.Position.DEFENDER], 5)
        self.assertEqual(counts[Player.Position.MIDFIELDER], 5)
        self.assertEqual(counts[Player.Position.FORWARD], 3)

    def test_squad_size_is_15(self):
        squad = select_squad(build_pool(), NEUTRAL_STRATEGY)
        self.assertEqual(len(squad), 15)

    def test_club_limit_respected(self):
        # Concentrate the best scores on a single club to make the limit
        # load-bearing - without it, the optimizer would happily take 6+
        # players from the same club.
        pool = build_pool(n_per_position=10, n_clubs=8)
        for sp in pool:
            if sp.player.team_id == 1:
                sp.total_score += 100
                sp.components.expected_component += 100
        squad = select_squad(pool, NEUTRAL_STRATEGY)
        club_counts = {}
        for sp in squad:
            club_counts[sp.player.team_id] = club_counts.get(sp.player.team_id, 0) + 1
        self.assertLessEqual(max(club_counts.values()), 3)

    def test_no_duplicate_players(self):
        squad = select_squad(build_pool(), NEUTRAL_STRATEGY)
        ids = [sp.player.id for sp in squad]
        self.assertEqual(len(ids), len(set(ids)))

    def test_unavailable_players_excluded(self):
        pool = build_pool()
        # Make the single highest scorer unavailable.
        best = max(pool, key=lambda sp: sp.total_score)
        best.components.availability_multiplier = 0.0
        squad = select_squad(pool, NEUTRAL_STRATEGY)
        self.assertNotIn(best.player.id, {sp.player.id for sp in squad})


class InfeasibilityTests(SimpleTestCase):
    def test_impossible_hard_constraint_raises(self):
        pool = build_pool(cheap=40, expensive=60)  # nobody is expensive
        impossible = StrategyConfig(
            name="impossible_premium",
            horizon=5,
            weights={"expected_component": 1.0},
            hard_constraints=(
                HardConstraint(type="min_count_above_price", price_tenths=200, count=2),
            ),
        )
        with self.assertRaises(InfeasibleStrategyError) as ctx:
            select_squad(pool, impossible)
        self.assertEqual(ctx.exception.strategy_name, "impossible_premium")

    def test_no_eligible_players_raises(self):
        pool = build_pool()
        for sp in pool:
            sp.components.availability_multiplier = 0.0
        with self.assertRaises(InfeasibleStrategyError):
            select_squad(pool, NEUTRAL_STRATEGY)

    def test_hard_constraint_satisfiable_squad_meets_it(self):
        pool = build_pool(n_per_position=8, cheap=40, expensive=140)
        strategy = StrategyConfig(
            name="premium_test",
            horizon=5,
            weights={"expected_component": 1.0},
            hard_constraints=(
                HardConstraint(type="min_count_above_price", price_tenths=110, count=2),
            ),
        )
        squad = select_squad(pool, strategy)
        above_threshold = sum(1 for sp in squad if sp.player.now_cost > 110)
        self.assertGreaterEqual(above_threshold, 2)


class DistinctStrategiesTests(SimpleTestCase):
    def test_ownership_weighted_strategy_diverges_from_neutral(self):
        from fpl_data.scoring.engine import combine_score

        pool = build_pool(n_per_position=10, n_clubs=8)
        # Give the highest scorers (by expected_component) high ownership
        # so a differential (negative ownership weight) strategy is
        # forced away from them once total_score is recombined.
        for sp in pool:
            sp.components.ownership_component = sp.components.expected_component

        differential = StrategyConfig(
            name="differential_test",
            horizon=5,
            weights={"expected_component": 1.0, "ownership_component": -2.0},
        )

        # select_squad only uses the strategy for hard constraints - the
        # objective comes from each ScoredPlayer's total_score, which must
        # be recombined per strategy first (this is score_player_pool's
        # job in production; done explicitly here for the test).
        neutral_pool = [
            ScoredPlayer(
                player=sp.player,
                components=sp.components,
                total_score=combine_score(sp.components, NEUTRAL_STRATEGY),
            )
            for sp in pool
        ]
        differential_pool = [
            ScoredPlayer(
                player=sp.player,
                components=sp.components,
                total_score=combine_score(sp.components, differential),
            )
            for sp in pool
        ]

        neutral_squad = {sp.player.id for sp in select_squad(neutral_pool, NEUTRAL_STRATEGY)}
        differential_squad = {
            sp.player.id for sp in select_squad(differential_pool, differential)
        }

        self.assertNotEqual(neutral_squad, differential_squad)


class ShortlistTests(SimpleTestCase):
    def test_shortlist_grouped_and_ranked_by_position(self):
        pool = build_pool(n_per_position=5)
        shortlist = shortlist_by_position(pool)
        midfielders = shortlist[Player.Position.MIDFIELDER]
        scores = [sp.total_score for sp in midfielders]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_shortlist_entries_carry_components(self):
        pool = build_pool(n_per_position=3)
        shortlist = shortlist_by_position(pool)
        entry = shortlist[Player.Position.FORWARD][0]
        self.assertIsInstance(entry.components, PlayerScoreComponents)

    def test_shortlist_reflects_strategy_weighting(self):
        pool = build_pool(n_per_position=6, n_clubs=6)
        for i, sp in enumerate(pool):
            sp.components.ownership_component = 5.0 + i

        neutral_shortlist = shortlist_by_position(pool)[Player.Position.FORWARD]

        differential_pool = []
        differential = StrategyConfig(
            name="d", horizon=5, weights={"ownership_component": -1.0}
        )
        from fpl_data.scoring.engine import combine_score

        for sp in pool:
            new_total = combine_score(sp.components, differential)
            differential_pool.append(
                ScoredPlayer(player=sp.player, components=sp.components, total_score=new_total)
            )
        differential_shortlist = shortlist_by_position(differential_pool)[
            Player.Position.FORWARD
        ]

        neutral_order = [sp.player.id for sp in neutral_shortlist]
        differential_order = [sp.player.id for sp in differential_shortlist]
        self.assertNotEqual(neutral_order, differential_order)
