from django.test import TestCase

from fpl_data.models import (
    Fixture,
    Player,
    PlayerGameweekHistory,
    PlayerSeasonHistory,
    Team,
)
from fpl_data.scoring.availability import (
    availability_multiplier,
    discipline_factor,
    fitness_ratio,
    status_multiplier,
)
from fpl_data.scoring.fixtures import (
    detect_blank_gameweeks,
    detect_double_gameweeks,
    fixture_count_factor,
    fixture_strength_ratio,
    mean_fixture_difficulty,
    resolve_team_strength,
)
from fpl_data.scoring.performance import (
    compute_player_components,
    gameweek_shaped_score,
    past_history_score,
    rotation_component,
    setpiece_component,
)


def make_team(id_=1, code=3, **overrides):
    defaults = dict(
        id=id_,
        code=code,
        name="Team",
        short_name="TM",
        strength=4,
        strength_overall_home=1200,
        strength_overall_away=1180,
        strength_attack_home=1210,
        strength_attack_away=1190,
        strength_defence_home=1220,
        strength_defence_away=1170,
    )
    defaults.update(overrides)
    team = Team(**defaults)
    team.save()
    return team


def make_player(id_=1, team=None, position=Player.Position.MIDFIELDER, **overrides):
    team = team or make_team()
    defaults = dict(
        id=id_,
        code=100000 + id_,
        first_name="Test",
        second_name="Player",
        web_name=f"Player{id_}",
        team=team,
        element_type=position,
        now_cost=80,
        status="a",
        selected_by_percent=10.0,
        starts_per_90=0.8,
        red_cards=0,
    )
    defaults.update(overrides)
    player = Player(**defaults)
    player.save()
    return player


class AvailabilityTests(TestCase):
    def test_suspended_ignores_chance_of_playing(self):
        self.assertEqual(availability_multiplier("s", None), 0.0)

    def test_available_with_null_chance_is_fully_available(self):
        self.assertEqual(availability_multiplier("a", None), 1.0)

    def test_doubtful_defers_to_fitness_percentage(self):
        self.assertEqual(availability_multiplier("d", 75), 0.75)

    def test_status_multiplier_values(self):
        self.assertEqual(status_multiplier("i"), 0.0)
        self.assertEqual(status_multiplier("s"), 0.0)
        self.assertEqual(status_multiplier("u"), 0.0)
        self.assertEqual(status_multiplier("a"), 1.0)
        self.assertEqual(status_multiplier("d"), 1.0)

    def test_fitness_ratio_null_is_fully_fit(self):
        self.assertEqual(fitness_ratio(None), 1.0)
        self.assertEqual(fitness_ratio(50), 0.5)


class DisciplineTests(TestCase):
    def test_no_red_cards(self):
        self.assertEqual(discipline_factor(0), 1.0)

    def test_multiple_red_cards_floors_at_085(self):
        self.assertEqual(discipline_factor(4), 0.85)


class GameweekShapedScoreTests(TestCase):
    def test_forward_no_xgc_subtracted(self):
        score = gameweek_shaped_score(
            ict_index=5.0,
            expected_goal_involvements=1.0,
            expected_goals_conceded=2.0,
            defensive_contribution=0.0,
            position=Player.Position.FORWARD,
        )
        self.assertEqual(score, 6.0)  # 5.0 + 1.0, xGC not subtracted

    def test_non_forward_has_xgc_subtracted(self):
        score = gameweek_shaped_score(
            ict_index=5.0,
            expected_goal_involvements=1.0,
            expected_goals_conceded=2.0,
            defensive_contribution=0.0,
            position=Player.Position.DEFENDER,
        )
        self.assertEqual(score, 4.0)  # 5.0 + 1.0 - 2.0

    def test_score_never_negative(self):
        score = gameweek_shaped_score(
            ict_index=1.0,
            expected_goal_involvements=0.0,
            expected_goals_conceded=10.0,
            defensive_contribution=0.0,
            position=Player.Position.DEFENDER,
        )
        self.assertEqual(score, 0.0)

    def test_involvement_counted_once_not_double(self):
        # xGI=0.6 should contribute exactly 0.6, not 1.2 (the prototype's
        # bug summed xG+xA+xGI, double counting).
        score = gameweek_shaped_score(
            ict_index=0.0,
            expected_goal_involvements=0.6,
            expected_goals_conceded=0.0,
            defensive_contribution=0.0,
            position=Player.Position.FORWARD,
        )
        self.assertAlmostEqual(score, 0.6)

    def test_goalkeeper_gets_no_defensive_term(self):
        with_dc = gameweek_shaped_score(0, 0, 0, 4.0, Player.Position.GOALKEEPER)
        self.assertEqual(with_dc, 0.0)

    def test_outfield_gets_halved_defensive_term(self):
        score = gameweek_shaped_score(0, 0, 0, 2.0, Player.Position.DEFENDER)
        self.assertEqual(score, 1.0)


class PastHistoryScoreTests(TestCase):
    def _season(self, player, season_name, minutes=2000, ict=10.0, xgi=1.0, xgc=0.5, dc90=0.2):
        return PlayerSeasonHistory(
            player=player,
            season_name=season_name,
            minutes=minutes,
            ict_index=ict,
            expected_goal_involvements=xgi,
            expected_goals_conceded=xgc,
            defensive_contribution_per_90=dc90,
        )

    def test_normalization_applied_once_regardless_of_season_count(self):
        player = make_player(position=Player.Position.MIDFIELDER)
        one_season = [self._season(player, "2024/25")]
        five_identical_seasons = [
            self._season(player, f"20{18+i}/{19+i}") for i in range(5)
        ]

        one_score = past_history_score(one_season, player.element_type, 2026)
        five_score = past_history_score(
            five_identical_seasons, player.element_type, 2026
        )

        # With 5 seasons contributing (vs 1), the accumulated total should
        # be roughly proportional to season count - NOT divided down by an
        # extra 5x on top of the single normalization divisor.
        self.assertGreater(five_score, one_score * 3)

    def test_no_past_seasons_returns_zero(self):
        player = make_player()
        self.assertEqual(past_history_score([], player.element_type, 2026), 0.0)


class SetpieceComponentTests(TestCase):
    def test_primary_penalty_taker_beats_non_taker(self):
        taker = make_player(id_=1, penalties_order=1)
        non_taker = make_player(id_=2, penalties_order=None)
        self.assertGreater(setpiece_component(taker), setpiece_component(non_taker))

    def test_first_choice_beats_second_choice(self):
        first = make_player(id_=1, direct_freekicks_order=1)
        second = make_player(id_=2, direct_freekicks_order=2)
        self.assertGreater(setpiece_component(first), setpiece_component(second))

    def test_no_order_contributes_nothing(self):
        player = make_player(
            penalties_order=None,
            direct_freekicks_order=None,
            corners_and_indirect_freekicks_order=None,
        )
        self.assertEqual(setpiece_component(player), 0.0)


class RotationComponentTests(TestCase):
    def test_consistent_starter_beats_rotation_risk_at_equal_output(self):
        consistent = make_player(id_=1, starts_per_90=0.9)
        risky = make_player(id_=2, starts_per_90=0.9)

        consistent_gws = [
            PlayerGameweekHistory(player=consistent, round=i, minutes=90)
            for i in range(1, 6)
        ]
        risky_gws = [
            PlayerGameweekHistory(player=risky, round=i, minutes=m)
            for i, m in enumerate([90, 5, 90, 0, 90], start=1)
        ]

        self.assertGreater(
            rotation_component(consistent, consistent_gws),
            rotation_component(risky, risky_gws),
        )

    def test_insufficient_data_falls_back_to_starts_per_90(self):
        player = make_player(starts_per_90=0.7)
        self.assertEqual(rotation_component(player, []), 0.7)


class TeamStrengthResolutionTests(TestCase):
    def test_position_specific_strength_used_when_present(self):
        team = make_team(strength_defence_home=1300)
        self.assertEqual(resolve_team_strength(team, "strength_defence", "home"), 1300)

    def test_falls_back_to_overall_when_position_specific_unset(self):
        team = make_team(strength_attack_home=0, strength_overall_home=1150)
        self.assertEqual(resolve_team_strength(team, "strength_attack", "home"), 1150)

    def test_returns_none_when_nothing_available(self):
        team = make_team(strength_attack_home=0, strength_overall_home=0)
        self.assertIsNone(resolve_team_strength(team, "strength_attack", "home"))


class FixtureStrengthRatioTests(TestCase):
    def test_neutral_ratio_when_no_strength_data(self):
        blank_team = make_team(
            id_=1,
            strength_attack_home=0,
            strength_attack_away=0,
            strength_defence_home=0,
            strength_defence_away=0,
            strength_overall_home=0,
            strength_overall_away=0,
        )
        opponent = make_team(id_=2)
        ratio = fixture_strength_ratio(
            Player.Position.FORWARD, blank_team, opponent, is_home=True
        )
        self.assertEqual(ratio, 1.0)

    def test_forward_uses_attack_vs_opponent_defence(self):
        strong_attack = make_team(id_=1, strength_attack_home=1600)
        weak_defence = make_team(id_=2, strength_defence_away=800)
        ratio = fixture_strength_ratio(
            Player.Position.FORWARD, strong_attack, weak_defence, is_home=True
        )
        self.assertEqual(ratio, 2.0)


class FixtureHorizonTests(TestCase):
    def setUp(self):
        self.team = make_team(id_=1, code=3)
        self.opponent = make_team(id_=2, code=7)
        self.player = make_player(id_=1, team=self.team, position=Player.Position.FORWARD)

    def _fixture(self, id_, event, team_h, team_a):
        return Fixture.objects.create(
            id=id_, event=event, team_h=team_h, team_a=team_a, finished=False
        )

    def test_no_fixtures_in_window_is_neutral(self):
        ratio, count = mean_fixture_difficulty(self.player, from_event=1, horizon=5)
        self.assertEqual((ratio, count), (1.0, 0))

    def test_horizon_bounds_fixtures_considered(self):
        for i in range(1, 9):
            self._fixture(i, i, self.team, self.opponent)
        _, count = mean_fixture_difficulty(self.player, from_event=1, horizon=5)
        self.assertEqual(count, 5)

    def test_horizon_exceeding_schedule_uses_what_remains(self):
        self._fixture(1, 1, self.team, self.opponent)
        self._fixture(2, 2, self.team, self.opponent)
        ratio, count = mean_fixture_difficulty(self.player, from_event=1, horizon=10)
        self.assertEqual(count, 2)
        self.assertIsInstance(ratio, float)

    def test_double_gameweek_both_fixtures_contribute(self):
        third_opponent = make_team(id_=3, code=11)
        self._fixture(1, 5, self.team, self.opponent)
        self._fixture(2, 5, self.team, third_opponent)
        _, count = mean_fixture_difficulty(self.player, from_event=5, horizon=1)
        self.assertEqual(count, 2)

    def test_fixture_count_factor(self):
        self.assertEqual(fixture_count_factor(1, 5), 0.2)
        self.assertEqual(fixture_count_factor(2, 1), 2.0)
        self.assertEqual(fixture_count_factor(0, 5), 0.0)


class DoubleBlankGameweekDetectionTests(TestCase):
    def setUp(self):
        self.team_a = make_team(id_=1, code=3)
        self.team_b = make_team(id_=2, code=7)
        self.team_c = make_team(id_=3, code=11)

    def test_double_gameweek_detected(self):
        Fixture.objects.create(id=1, event=10, team_h=self.team_a, team_a=self.team_b)
        Fixture.objects.create(id=2, event=10, team_h=self.team_a, team_a=self.team_c)
        doubles = detect_double_gameweeks(self.team_a.id, from_event=10, horizon=1)
        self.assertEqual(doubles, {10: 2})

    def test_blank_gameweek_detected(self):
        Fixture.objects.create(id=1, event=12, team_h=self.team_b, team_a=self.team_c)
        blanks = detect_blank_gameweeks(self.team_a.id, from_event=12, horizon=1)
        self.assertEqual(blanks, {12})

    def test_no_blank_when_team_plays(self):
        Fixture.objects.create(id=1, event=12, team_h=self.team_a, team_a=self.team_b)
        blanks = detect_blank_gameweeks(self.team_a.id, from_event=12, horizon=1)
        self.assertEqual(blanks, set())


class PreseasonScoringTests(TestCase):
    """See "Scoring Degrades Explicitly Without Current-Season History"
    and "Players Without Any History Are Identifiable"."""

    def setUp(self):
        self.team = make_team()
        self.opponent = make_team(id_=2, code=7)

    def test_preseason_with_past_history_scores_nonzero(self):
        player = make_player(position=Player.Position.MIDFIELDER)
        PlayerSeasonHistory.objects.create(
            player=player,
            season_name="2025/26",
            minutes=3000,
            ict_index=15.0,
            expected_goal_involvements=2.0,
            expected_goals_conceded=1.0,
        )
        Fixture.objects.create(
            id=1, event=1, team_h=player.team, team_a=self.opponent, finished=False
        )

        components = compute_player_components(
            player,
            gameweek_histories=[],
            season_histories=[player.season_history.get()],
            next_event=1,
            horizon=5,
            current_season_start_year=2026,
        )

        self.assertGreater(components.expected_component, 0.0)
        self.assertTrue(components.has_history)
        self.assertEqual(components.realized_component, 0.0)
        self.assertEqual(components.regression_signal, 0.0)

    def test_no_history_at_all_is_flagged(self):
        player = make_player(total_points=0, expected_goals=0.0, goals_scored=0)
        components = compute_player_components(
            player,
            gameweek_histories=[],
            season_histories=[],
            next_event=1,
            horizon=5,
            current_season_start_year=2026,
        )
        self.assertFalse(components.has_history)
        self.assertEqual(components.expected_component, 0.0)

    def test_no_division_by_zero_gameweeks(self):
        player = make_player()
        # Should not raise.
        compute_player_components(
            player,
            gameweek_histories=[],
            season_histories=[],
            next_event=1,
            horizon=5,
            current_season_start_year=2026,
        )

    def test_ownership_component_is_raw_unsigned(self):
        player = make_player(selected_by_percent=42.5)
        components = compute_player_components(
            player,
            gameweek_histories=[],
            season_histories=[],
            next_event=1,
            horizon=5,
            current_season_start_year=2026,
        )
        self.assertEqual(components.ownership_component, 42.5)
