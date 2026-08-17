from django.test import SimpleTestCase, TestCase

from fpl_data.backtesting import (
    ablatable_factors,
    ablate,
    captain_outscored_median,
    highest_ownership_squad,
    price_only_squad,
    random_legal_squad,
    realized_points,
    run_backtest,
    score_pool_as_of,
)
from fpl_data.models import Fixture, Player, PlayerGameweekHistory, Team
from fpl_data.scoring.engine import ScoredPlayer
from fpl_data.scoring.performance import PlayerScoreComponents
from fpl_data.strategies import OBJECTIVE_TERMS, get_strategy


def make_team(id_, code):
    return Team.objects.create(
        id=id_,
        code=code,
        name=f"Team{id_}",
        short_name=f"T{id_}",
        strength=4,
        strength_overall_home=1200,
        strength_overall_away=1180,
        strength_attack_home=1200,
        strength_attack_away=1180,
        strength_defence_home=1200,
        strength_defence_away=1180,
    )


def make_player(id_, team, position, now_cost=80):
    return Player.objects.create(
        id=id_,
        code=100000 + id_,
        first_name="Test",
        second_name=f"Player{id_}",
        web_name=f"Player{id_}",
        team=team,
        element_type=position,
        now_cost=now_cost,
        selected_by_percent=10.0,
    )


_POSITION_CLUB_OFFSETS = {
    Player.Position.GOALKEEPER: 0,
    Player.Position.DEFENDER: 2,
    Player.Position.MIDFIELDER: 4,
    Player.Position.FORWARD: 6,
}


def build_full_pool(n_per_position=8, n_clubs=8, cheap=40, expensive=140):
    """Real (saved) Team/Player rows spread across clubs so budget + club
    constraints stay jointly feasible - same decorrelation as
    test_optimization.build_pool, needed here because baseline builders
    call the real select_squad."""
    teams = {i: make_team(i, i + 2) for i in range(1, n_clubs + 1)}
    pool = []
    pid = 1
    for position, offset in _POSITION_CLUB_OFFSETS.items():
        for i in range(n_per_position):
            team_id = ((i + offset) % n_clubs) + 1
            price = cheap + (expensive - cheap) * (i / max(n_per_position - 1, 1))
            player = make_player(pid, teams[team_id], position, now_cost=int(price))
            components = PlayerScoreComponents(
                expected_component=5.0 + i,
                realized_component=0.0,
                regression_signal=0.0,
                fixture_component=1.0,
                setpiece_component=0.0,
                ownership_component=float(i),
                rotation_component=1.0,
                availability_multiplier=1.0,
                discipline_multiplier=1.0,
                has_history=True,
                next_gw_score=5.0 + i,
            )
            pool.append(
                ScoredPlayer(player=player, components=components, total_score=5.0 + i)
            )
            pid += 1
    return pool


class ScorePoolAsOfTests(TestCase):
    def test_future_gameweek_history_does_not_affect_score(self):
        team = make_team(1, 3)
        opponent = make_team(2, 7)
        player = make_player(1, team, Player.Position.FORWARD)
        strategy = get_strategy("balanced")

        PlayerGameweekHistory.objects.create(
            player=player, round=1, minutes=90, ict_index=5.0,
            expected_goal_involvements=1.0, expected_goals_conceded=0.0,
        )
        # A much bigger future gameweek that must not leak into a replay
        # from event=3.
        PlayerGameweekHistory.objects.create(
            player=player, round=5, minutes=90, ict_index=50.0,
            expected_goal_involvements=10.0, expected_goals_conceded=0.0,
        )
        Fixture.objects.create(id=1, event=3, team_h=team, team_a=opponent, finished=False)

        [scored_at_3] = list(score_pool_as_of(strategy, 2026, replay_event=3))
        [scored_at_6] = list(score_pool_as_of(strategy, 2026, replay_event=6))

        self.assertNotAlmostEqual(
            scored_at_3.components.expected_component,
            scored_at_6.components.expected_component,
        )

    def test_only_rounds_before_replay_event_are_used(self):
        team = make_team(1, 3)
        player = make_player(1, team, Player.Position.MIDFIELDER)
        strategy = get_strategy("balanced")

        for round_ in (1, 2, 3, 4):
            PlayerGameweekHistory.objects.create(
                player=player, round=round_, minutes=90, ict_index=1.0,
            )

        [scored] = list(score_pool_as_of(strategy, 2026, replay_event=3))
        # rounds 1 and 2 only (< 3) should have been used - 2 played gws.
        self.assertGreater(scored.components.expected_component, 0)


class RealizedPointsTests(TestCase):
    def test_sums_only_the_requested_window(self):
        team = make_team(1, 3)
        player = make_player(1, team, Player.Position.MIDFIELDER)
        for round_, points in [(1, 2), (2, 4), (3, 6), (4, 8), (5, 10), (6, 100)]:
            PlayerGameweekHistory.objects.create(
                player=player, round=round_, total_points=points, minutes=90
            )
        # window [3, 6) = rounds 3, 4, 5 -> 6+8+10 = 24
        self.assertEqual(realized_points([player.id], from_event=3, horizon=3), 24)

    def test_empty_player_list_is_zero(self):
        self.assertEqual(realized_points([], from_event=1, horizon=5), 0)


class BaselineConstraintTests(TestCase):
    def test_random_legal_squad_satisfies_constraints(self):
        pool = build_full_pool()
        strategy = get_strategy("balanced")
        squad = random_legal_squad(pool, strategy)
        self._assert_legal(squad)

    def test_highest_ownership_squad_satisfies_constraints(self):
        pool = build_full_pool()
        strategy = get_strategy("balanced")
        squad = highest_ownership_squad(pool, strategy)
        self._assert_legal(squad)

    def test_price_only_squad_satisfies_constraints(self):
        pool = build_full_pool()
        strategy = get_strategy("balanced")
        squad = price_only_squad(pool, strategy)
        self._assert_legal(squad)

    def test_price_only_squad_favours_expensive_players(self):
        pool = build_full_pool()
        strategy = get_strategy("balanced")
        squad = price_only_squad(pool, strategy)
        total_price = sum(sp.player.now_cost for sp in squad)
        # Maximizing spend under a 1000-tenths budget across 15 players
        # should land close to the 1000/15 ceiling - nowhere near the
        # pool's cheap end (a legal squad can cost as little as ~600).
        self.assertGreater(total_price, 900)

    def _assert_legal(self, squad):
        self.assertEqual(len(squad), 15)
        total_price = sum(sp.player.now_cost for sp in squad)
        self.assertLessEqual(total_price, 1000)
        counts = {}
        clubs = {}
        for sp in squad:
            counts[sp.player.element_type] = counts.get(sp.player.element_type, 0) + 1
            clubs[sp.player.team_id] = clubs.get(sp.player.team_id, 0) + 1
        self.assertEqual(counts[Player.Position.GOALKEEPER], 2)
        self.assertEqual(counts[Player.Position.DEFENDER], 5)
        self.assertEqual(counts[Player.Position.MIDFIELDER], 5)
        self.assertEqual(counts[Player.Position.FORWARD], 3)
        self.assertLessEqual(max(clubs.values()), 3)
        ids = [sp.player.id for sp in squad]
        self.assertEqual(len(ids), len(set(ids)))


class AblationTests(SimpleTestCase):
    def test_every_objective_term_is_ablatable(self):
        self.assertEqual(ablatable_factors(), list(OBJECTIVE_TERMS))

    def test_ablate_zeros_only_the_named_factor(self):
        strategy = get_strategy("balanced")
        ablated = ablate(strategy, "ownership_component")

        self.assertEqual(ablated.weight("ownership_component"), 0.0)
        for term in OBJECTIVE_TERMS:
            if term != "ownership_component":
                self.assertEqual(ablated.weight(term), strategy.weight(term))

    def test_ablate_preserves_horizon_and_hard_constraints(self):
        strategy = get_strategy("premium_heavy")
        ablated = ablate(strategy, "expected_component")
        self.assertEqual(ablated.horizon, strategy.horizon)
        self.assertEqual(ablated.hard_constraints, strategy.hard_constraints)

    def test_unknown_factor_raises(self):
        strategy = get_strategy("balanced")
        with self.assertRaises(ValueError):
            ablate(strategy, "not_a_real_factor")


class CaptainAccuracyTests(TestCase):
    def test_captain_above_median_is_true(self):
        team = make_team(1, 3)
        players = [make_player(i, team, Player.Position.MIDFIELDER) for i in range(1, 6)]
        points = [1, 2, 3, 4, 5]
        for p, pts in zip(players, points):
            PlayerGameweekHistory.objects.create(player=p, round=1, total_points=pts, minutes=90)

        # median of [1,2,3,4,5] is 3; player 5 scored 5 > 3
        self.assertTrue(
            captain_outscored_median([p.id for p in players], captain_id=5, event=1)
        )

    def test_captain_at_or_below_median_is_false(self):
        team = make_team(1, 3)
        players = [make_player(i, team, Player.Position.MIDFIELDER) for i in range(1, 6)]
        points = [1, 2, 3, 4, 5]
        for p, pts in zip(players, points):
            PlayerGameweekHistory.objects.create(player=p, round=1, total_points=pts, minutes=90)

        self.assertFalse(
            captain_outscored_median([p.id for p in players], captain_id=3, event=1)
        )

    def test_even_count_uses_averaged_median(self):
        team = make_team(1, 3)
        players = [make_player(i, team, Player.Position.MIDFIELDER) for i in range(1, 5)]
        points = [1, 2, 3, 4]  # median = 2.5
        for p, pts in zip(players, points):
            PlayerGameweekHistory.objects.create(player=p, round=1, total_points=pts, minutes=90)

        self.assertTrue(
            captain_outscored_median([p.id for p in players], captain_id=3, event=1)
        )  # 3 > 2.5
        self.assertFalse(
            captain_outscored_median([p.id for p in players], captain_id=2, event=1)
        )  # 2 < 2.5


class RunBacktestIntegrationTests(TestCase):
    def setUp(self):
        n_clubs = 8
        self.teams = {i: make_team(i, i + 2) for i in range(1, n_clubs + 1)}
        self.opponent_pool = list(self.teams.values())

        self.players = []
        pid = 1
        for position, offset in _POSITION_CLUB_OFFSETS.items():
            for i in range(8):
                team_id = ((i + offset) % n_clubs) + 1
                price = 40 + (140 - 40) * (i / 7)
                player = make_player(pid, self.teams[team_id], position, now_cost=int(price))
                self.players.append(player)
                pid += 1

        # Gameweek history for rounds 1-6 so replay events 3-4 (horizon
        # covers up to round 6 or so) have realized points to sum.
        for round_ in range(1, 7):
            for player in self.players:
                PlayerGameweekHistory.objects.create(
                    player=player,
                    round=round_,
                    minutes=90,
                    total_points=round_ + (player.id % 5),
                    ict_index=2.0,
                    expected_goal_involvements=0.3,
                )

        # Fixtures so fixture-difficulty lookups don't error, one per club
        # per gameweek against a rotating opponent.
        fid = 1
        for round_ in range(1, 9):
            for i, team in enumerate(self.opponent_pool):
                opponent = self.opponent_pool[(i + 1) % len(self.opponent_pool)]
                if team.id < opponent.id:
                    Fixture.objects.create(
                        id=fid, event=round_, team_h=team, team_a=opponent, finished=round_ <= 2
                    )
                    fid += 1

    def test_produces_one_result_per_feasible_replay_point(self):
        results = run_backtest("balanced", [3, 4], season_start_year=2026)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIn("random", r.baseline_realized_points)
            self.assertIn("template", r.baseline_realized_points)
            self.assertIn("price_only", r.baseline_realized_points)
            self.assertIn("season", r.captain_outscored_median)
            self.assertIn("next_gw", r.captain_outscored_median)
            self.assertIn("differential", r.captain_outscored_median)

    def test_ablated_run_still_produces_results(self):
        results = run_backtest(
            "balanced", [3], season_start_year=2026, ablate_factor="setpiece_component"
        )
        self.assertEqual(len(results), 1)
