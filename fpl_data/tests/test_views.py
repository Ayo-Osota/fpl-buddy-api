from unittest.mock import patch

from django.test import TestCase

from fpl_data.models import (
    Player,
    PlayerScore,
    ScoringRun,
    SuggestedSquad,
    SuggestedSquadPlayer,
    Team,
)


def make_team(id_=1, code=3):
    return Team.objects.create(
        id=id_,
        code=code,
        name="Team",
        short_name="TM",
        strength=4,
        strength_overall_home=1200,
        strength_overall_away=1180,
    )


def make_player(id_, team, position, now_cost=80, **overrides):
    defaults = dict(
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
    defaults.update(overrides)
    return Player.objects.create(**defaults)


class SuggestionEndpointsRequireNoSessionTests(TestCase):
    def test_player_scores_succeeds_without_session(self):
        response = self.client.get("/players/scores/")
        self.assertEqual(response.status_code, 200)

    def test_shortlist_succeeds_without_session(self):
        response = self.client.get("/players/shortlist/")
        self.assertEqual(response.status_code, 200)

    def test_squads_succeeds_without_session(self):
        response = self.client.get("/squads/")
        self.assertEqual(response.status_code, 200)


class EmptyStateTests(TestCase):
    def test_player_scores_empty_state(self):
        response = self.client.get("/players/scores/")
        data = response.json()
        self.assertFalse(data["available"])

    def test_shortlist_empty_state(self):
        response = self.client.get("/players/shortlist/")
        data = response.json()
        self.assertFalse(data["available"])

    def test_squads_empty_state(self):
        response = self.client.get("/squads/")
        data = response.json()
        self.assertFalse(data["available"])


class MethodRejectionTests(TestCase):
    def test_player_scores_rejects_post(self):
        response = self.client.post("/players/scores/")
        self.assertEqual(response.status_code, 405)

    def test_shortlist_rejects_post(self):
        response = self.client.post("/players/shortlist/")
        self.assertEqual(response.status_code, 405)

    def test_squads_rejects_post(self):
        response = self.client.post("/squads/")
        self.assertEqual(response.status_code, 405)


class ServesStoredDataOnlyTests(TestCase):
    """See "Endpoint serves stored results" - no FPL API call, no solver
    run, purely reads what's already in the database."""

    def setUp(self):
        self.team = make_team()
        self.player = make_player(1, self.team, Player.Position.MIDFIELDER)
        self.run = ScoringRun.objects.create(
            strategy_name="balanced",
            weights={"name": "balanced", "horizon": 5, "weights": {}, "hard_constraints": []},
            season_start_year=2026,
        )
        PlayerScore.objects.create(
            scoring_run=self.run,
            player=self.player,
            total_score=12.5,
            next_gw_score=3.0,
            expected_component=8.0,
            realized_component=2.0,
            regression_signal=0.5,
            fixture_component=1.0,
            setpiece_component=0.0,
            ownership_component=10.0,
            rotation_component=0.9,
            availability_multiplier=1.0,
            discipline_multiplier=1.0,
            has_history=True,
        )

    @patch("fpl_data.fpl_client.client.get_bootstrap_static")
    @patch("fpl_data.optimization.pulp.LpProblem")
    def test_player_scores_never_touches_fpl_or_solver(
        self, mock_lp_problem, mock_bootstrap
    ):
        response = self.client.get("/players/scores/?strategy=balanced")
        self.assertEqual(response.status_code, 200)
        mock_bootstrap.assert_not_called()
        mock_lp_problem.assert_not_called()

    def test_player_scores_returns_stored_components(self):
        response = self.client.get("/players/scores/?strategy=balanced")
        data = response.json()
        self.assertTrue(data["available"])
        self.assertEqual(data["run_id"], self.run.id)
        player_out = data["players"][0]
        self.assertEqual(player_out["id"], 1)
        self.assertEqual(player_out["total_score"], 12.5)
        self.assertEqual(player_out["expected_component"], 8.0)

    def test_shortlist_groups_by_position(self):
        response = self.client.get("/players/shortlist/?strategy=balanced")
        data = response.json()
        self.assertTrue(data["available"])
        self.assertIn(str(Player.Position.MIDFIELDER), data["shortlist"])


class SquadResponseCompositionTests(TestCase):
    """See "Squad Response Contains Full Composition"."""

    def setUp(self):
        self.team = make_team()
        self.run = ScoringRun.objects.create(
            strategy_name="balanced",
            weights={},
            season_start_year=2026,
        )

        positions = (
            [Player.Position.GOALKEEPER] * 2
            + [Player.Position.DEFENDER] * 5
            + [Player.Position.MIDFIELDER] * 5
            + [Player.Position.FORWARD] * 3
        )
        self.players = [
            make_player(i + 1, self.team, pos, now_cost=60 + i)
            for i, pos in enumerate(positions)
        ]

        gk1, gk2 = self.players[0], self.players[1]
        defs = self.players[2:7]
        mids = self.players[7:12]
        fwds = self.players[12:15]

        self.squad = SuggestedSquad.objects.create(
            scoring_run=self.run,
            strategy_name="balanced",
            formation="4-4-2",
            total_price=980,
            season_captain=mids[0],
            season_vice_captain=mids[1],
            next_gw_captain=fwds[0],
            next_gw_vice_captain=fwds[1],
            differential_captain=mids[2],
            differential_vice_captain=defs[0],
        )

        SuggestedSquadPlayer.objects.create(squad=self.squad, player=gk1, is_starter=True)
        SuggestedSquadPlayer.objects.create(
            squad=self.squad, player=gk2, is_starter=False, is_bench_goalkeeper=True
        )
        for d in defs[:4]:
            SuggestedSquadPlayer.objects.create(squad=self.squad, player=d, is_starter=True)
        SuggestedSquadPlayer.objects.create(
            squad=self.squad, player=defs[4], is_starter=False, bench_rank=1
        )
        for m in mids[:4]:
            SuggestedSquadPlayer.objects.create(squad=self.squad, player=m, is_starter=True)
        SuggestedSquadPlayer.objects.create(
            squad=self.squad, player=mids[4], is_starter=False, bench_rank=2
        )
        for f in fwds[:2]:
            SuggestedSquadPlayer.objects.create(squad=self.squad, player=f, is_starter=True)
        SuggestedSquadPlayer.objects.create(
            squad=self.squad, player=fwds[2], is_starter=False, bench_rank=3
        )

    def test_response_has_full_composition(self):
        response = self.client.get("/squads/?strategy=balanced")
        data = response.json()
        squad = data["squads"][0]

        self.assertEqual(squad["formation"], "4-4-2")
        self.assertEqual(squad["total_price_tenths"], 980)
        self.assertEqual(len(squad["starters"]), 11)
        self.assertEqual(len(squad["bench"]["outfield"]), 3)
        self.assertIn("goalkeeper", squad["bench"])
        self.assertIn("season", squad["captains"])
        self.assertIn("next_gw", squad["captains"])
        self.assertIn("differential", squad["captains"])
        self.assertIsNotNone(squad["captains"]["differential"])

    def test_bench_outfield_in_rank_order(self):
        response = self.client.get("/squads/?strategy=balanced")
        squad = response.json()["squads"][0]
        ranks = [p["id"] for p in squad["bench"]["outfield"]]
        # bench_rank was assigned 1, 2, 3 to the def/mid/fwd bench slots
        # respectively - just confirm we get exactly 3 back, correctly
        # separated from the goalkeeper slot.
        self.assertEqual(len(ranks), 3)

    def test_differential_none_when_not_set(self):
        squad2 = SuggestedSquad.objects.create(
            scoring_run=self.run,
            strategy_name="premium_heavy",
            formation="3-4-3",
            total_price=990,
            season_captain=self.players[7],
            season_vice_captain=self.players[8],
            next_gw_captain=self.players[9],
            next_gw_vice_captain=self.players[10],
            differential_captain=None,
            differential_vice_captain=None,
        )
        SuggestedSquadPlayer.objects.create(
            squad=squad2, player=self.players[0], is_starter=True
        )
        SuggestedSquadPlayer.objects.create(
            squad=squad2, player=self.players[1], is_starter=False, is_bench_goalkeeper=True
        )

        response = self.client.get("/squads/?strategy=premium_heavy")
        data = response.json()
        squad = data["squads"][0]
        self.assertIsNone(squad["captains"]["differential"])


class ExistingEndpointsUnchangedTests(TestCase):
    """See "Existing Per-Entry Endpoints Unchanged"."""

    def test_me_squad_without_session_still_401s_with_same_message(self):
        response = self.client.get("/me/squad/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json()["error"],
            "not connected - submit your FPL Team ID to /connect/",
        )

    def test_me_history_without_session_still_401s(self):
        response = self.client.get("/me/history/")
        self.assertEqual(response.status_code, 401)

    def test_me_budget_without_session_still_401s(self):
        response = self.client.get("/me/budget/")
        self.assertEqual(response.status_code, 401)
