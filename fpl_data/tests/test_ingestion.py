from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from fpl_data import ingestion
from fpl_data.models import (
    Fixture,
    FplDataCache,
    Player,
    PlayerGameweekHistory,
    PlayerSeasonHistory,
    Team,
)


def _team_payload(team_id=1, code=3):
    return {
        "id": team_id,
        "code": code,
        "name": "Arsenal",
        "short_name": "ARS",
        "strength": 4,
        "strength_overall_home": 1200,
        "strength_overall_away": 1180,
        "strength_attack_home": 1210,
        "strength_attack_away": 1190,
        "strength_defence_home": 1220,
        "strength_defence_away": 1170,
    }


def _player_payload(player_id, team_id=1, status="a", chance=None):
    return {
        "id": player_id,
        "code": 100000 + player_id,
        "first_name": "Test",
        "second_name": f"Player{player_id}",
        "web_name": f"Player{player_id}",
        "team": team_id,
        "element_type": 3,
        "now_cost": 80,
        "status": status,
        "chance_of_playing_next_round": chance,
        "news": "",
        "selected_by_percent": "12.3",
        "total_points": 10,
        "form": "4.5",
        "points_per_game": "3.3",
        "minutes": 900,
        "starts": 10,
        "starts_per_90": 0.9,
        "goals_scored": 2,
        "assists": 1,
        "clean_sheets": 3,
        "goals_conceded": 5,
        "own_goals": 0,
        "penalties_saved": 0,
        "penalties_missed": 0,
        "saves": 0,
        "bonus": 4,
        "yellow_cards": 1,
        "red_cards": 0,
        "influence": "50.0",
        "creativity": "60.0",
        "threat": "70.0",
        "ict_index": "18.0",
        "influence_rank": 100,
        "creativity_rank": 90,
        "threat_rank": 80,
        "ict_index_rank": 70,
        "expected_goals": "1.5",
        "expected_assists": "0.8",
        "expected_goal_involvements": "2.3",
        "expected_goals_conceded": "4.0",
        "expected_goals_per_90": "0.15",
        "expected_assists_per_90": "0.08",
        "saves_per_90": 0.0,
        "clean_sheets_per_90": 0.3,
        "defensive_contribution": 1.0,
        "defensive_contribution_per_90": 0.1,
        "penalties_order": None,
        "direct_freekicks_order": 1,
        "corners_and_indirect_freekicks_order": None,
    }


def _bootstrap_static(player_ids=(1, 2, 3), statuses=None):
    statuses = statuses or {}
    return {
        "events": [{"id": 1, "deadline_time": "2026-08-15T17:30:00Z"}],
        "teams": [_team_payload()],
        "elements": [
            _player_payload(pid, status=statuses.get(pid, "a"))
            for pid in player_ids
        ],
    }


def _element_summary(history=None, history_past=None):
    return {
        "fixtures": [],
        "history": history or [],
        "history_past": history_past or [],
    }


class DeriveCurrentSeasonTests(TestCase):
    def test_deadline_in_july_or_later_is_that_years_season(self):
        bs = {"events": [{"deadline_time": "2026-08-15T17:30:00Z"}]}
        self.assertEqual(ingestion.derive_current_season_start_year(bs), 2026)

    def test_deadline_before_july_belongs_to_previous_years_season(self):
        bs = {"events": [{"deadline_time": "2027-05-10T17:30:00Z"}]}
        self.assertEqual(ingestion.derive_current_season_start_year(bs), 2026)

    def test_no_events_raises(self):
        with self.assertRaises(ValueError):
            ingestion.derive_current_season_start_year({"events": []})


class IngestTeamsAndPlayersTests(TestCase):
    def test_ingest_teams_upserts(self):
        bs = _bootstrap_static()
        ingestion.ingest_teams(bs)
        team = Team.objects.get(id=1)
        self.assertEqual(team.short_name, "ARS")
        self.assertEqual(team.strength_attack_home, 1210)

    def test_ingest_players_coerces_string_floats(self):
        bs = _bootstrap_static()
        ingestion.ingest_teams(bs)
        ingestion.ingest_players(bs)
        player = Player.objects.get(id=1)
        self.assertAlmostEqual(player.selected_by_percent, 12.3)
        self.assertAlmostEqual(player.expected_goals, 1.5)
        self.assertEqual(player.now_cost, 80)
        self.assertIsNone(player.penalties_order)
        self.assertEqual(player.direct_freekicks_order, 1)

    def test_ingest_players_is_queryable_by_position(self):
        bs = _bootstrap_static()
        ingestion.ingest_teams(bs)
        ingestion.ingest_players(bs)
        midfielders = Player.objects.filter(element_type=Player.Position.MIDFIELDER)
        self.assertEqual(midfielders.count(), 3)


class StalenessTests(TestCase):
    def setUp(self):
        bs = _bootstrap_static(player_ids=(1, 2, 3))
        ingestion.ingest_teams(bs)
        ingestion.ingest_players(bs)

    def test_player_with_no_summary_is_stale(self):
        self.assertIn(1, ingestion.stale_player_ids())

    @override_settings(FPL_GLOBAL_DATA_FRESHNESS_SECONDS=3600)
    def test_fresh_summary_is_not_stale(self):
        Player.objects.filter(id=1).update(summary_fetched_at=timezone.now())
        self.assertNotIn(1, ingestion.stale_player_ids())

    @override_settings(FPL_GLOBAL_DATA_FRESHNESS_SECONDS=3600)
    def test_old_summary_is_stale(self):
        old = timezone.now() - timedelta(hours=2)
        Player.objects.filter(id=1).update(summary_fetched_at=old)
        self.assertIn(1, ingestion.stale_player_ids())


class UnavailablePlayerTests(TestCase):
    def test_injured_suspended_unavailable_are_flagged(self):
        bs = _bootstrap_static(
            player_ids=(1, 2, 3, 4),
            statuses={1: "i", 2: "s", 3: "u", 4: "a"},
        )
        ingestion.ingest_teams(bs)
        ingestion.ingest_players(bs)
        skip = set(ingestion.unavailable_player_ids())
        self.assertEqual(skip, {1, 2, 3})

    def test_doubtful_with_zero_chance_is_flagged(self):
        bs = _bootstrap_static(player_ids=(1,))
        bs["elements"][0]["status"] = "d"
        bs["elements"][0]["chance_of_playing_next_round"] = 0
        ingestion.ingest_teams(bs)
        ingestion.ingest_players(bs)
        self.assertIn(1, ingestion.unavailable_player_ids())

    def test_doubtful_with_nonzero_chance_is_not_flagged(self):
        bs = _bootstrap_static(player_ids=(1,))
        bs["elements"][0]["status"] = "d"
        bs["elements"][0]["chance_of_playing_next_round"] = 50
        ingestion.ingest_teams(bs)
        ingestion.ingest_players(bs)
        self.assertNotIn(1, ingestion.unavailable_player_ids())


class IngestPlayerSummaryTests(TestCase):
    def setUp(self):
        bs = _bootstrap_static(player_ids=(1,))
        ingestion.ingest_teams(bs)
        ingestion.ingest_players(bs)

    @patch("fpl_data.ingestion.client")
    def test_persists_current_season_and_past_season_history(self, mock_client):
        mock_client.get_element_summary.return_value = _element_summary(
            history=[
                {
                    "round": 1,
                    "opponent_team": 1,
                    "was_home": True,
                    "total_points": 6,
                    "minutes": 90,
                    "expected_goals": "0.4",
                    "expected_assists": "0.1",
                    "expected_goal_involvements": "0.5",
                    "expected_goals_conceded": "1.2",
                    "ict_index": "10.0",
                }
            ],
            history_past=[
                {
                    "season_name": "2024/25",
                    "total_points": 150,
                    "minutes": 2500,
                    "expected_goals": "8.0",
                }
            ],
        )
        ingestion.ingest_player_summary(1)

        gw_row = PlayerGameweekHistory.objects.get(player_id=1, round=1)
        self.assertEqual(gw_row.total_points, 6)
        self.assertAlmostEqual(gw_row.expected_goal_involvements, 0.5)

        season_row = PlayerSeasonHistory.objects.get(player_id=1, season_name="2024/25")
        self.assertEqual(season_row.total_points, 150)

        player = Player.objects.get(id=1)
        self.assertIsNotNone(player.summary_fetched_at)

    @patch("fpl_data.ingestion.client")
    def test_no_current_season_history_persists_nothing_but_does_not_error(
        self, mock_client
    ):
        mock_client.get_element_summary.return_value = _element_summary()
        ingestion.ingest_player_summary(1)
        self.assertEqual(PlayerGameweekHistory.objects.filter(player_id=1).count(), 0)
        self.assertIsNotNone(Player.objects.get(id=1).summary_fetched_at)


class RunFullIngestionTests(TestCase):
    @patch("fpl_data.ingestion.client")
    def test_skips_unavailable_and_fetches_only_stale(self, mock_client):
        mock_client.get_bootstrap_static.return_value = _bootstrap_static(
            player_ids=(1, 2, 3), statuses={1: "i"}
        )
        mock_client.get_fixtures.return_value = []
        mock_client.get_element_summary.return_value = _element_summary()

        result = ingestion.run_full_ingestion()

        self.assertEqual(result["players_total"], 3)
        self.assertEqual(result["players_skipped_unavailable"], 1)
        self.assertEqual(result["players_fetched"], 2)
        fetched_ids = {call.args[0] for call in mock_client.get_element_summary.call_args_list}
        self.assertEqual(fetched_ids, {2, 3})

    @patch("fpl_data.ingestion.client")
    def test_resumed_run_skips_already_fresh_players(self, mock_client):
        mock_client.get_bootstrap_static.return_value = _bootstrap_static(
            player_ids=(1, 2)
        )
        mock_client.get_fixtures.return_value = []
        mock_client.get_element_summary.return_value = _element_summary()

        ingestion.run_full_ingestion()
        self.assertEqual(mock_client.get_element_summary.call_count, 2)

        mock_client.get_element_summary.reset_mock()
        ingestion.run_full_ingestion()
        self.assertEqual(mock_client.get_element_summary.call_count, 0)

    @patch("fpl_data.ingestion.client")
    def test_failure_on_one_player_does_not_lose_others(self, mock_client):
        mock_client.get_bootstrap_static.return_value = _bootstrap_static(
            player_ids=(1, 2, 3)
        )
        mock_client.get_fixtures.return_value = []

        def side_effect(player_id):
            if player_id == 2:
                raise ValueError("simulated FPL error")
            return _element_summary()

        mock_client.get_element_summary.side_effect = side_effect

        result = ingestion.run_full_ingestion()

        self.assertEqual(result["players_failed"], [2])
        self.assertIsNotNone(Player.objects.get(id=1).summary_fetched_at)
        self.assertIsNone(Player.objects.get(id=2).summary_fetched_at)
        self.assertIsNotNone(Player.objects.get(id=3).summary_fetched_at)

    @patch("fpl_data.ingestion.client")
    def test_does_not_touch_fpl_data_cache(self, mock_client):
        FplDataCache.objects.create(
            fpl_team_id=999, resource_type="summary", gw=None, payload={"untouched": True}
        )
        mock_client.get_bootstrap_static.return_value = _bootstrap_static(player_ids=(1,))
        mock_client.get_fixtures.return_value = []
        mock_client.get_element_summary.return_value = _element_summary()

        ingestion.run_full_ingestion()

        cache_row = FplDataCache.objects.get(fpl_team_id=999)
        self.assertEqual(cache_row.payload, {"untouched": True})
        self.assertEqual(FplDataCache.objects.count(), 1)


class FixturesIngestionTests(TestCase):
    def test_ingest_fixtures_upserts(self):
        ingestion.ingest_teams(_bootstrap_static())
        Team.objects.get_or_create(
            id=2, defaults={**_team_payload(team_id=2, code=7), "id": 2}
        )
        with patch("fpl_data.ingestion.client") as mock_client:
            mock_client.get_fixtures.return_value = [
                {
                    "id": 501,
                    "event": 1,
                    "team_h": 1,
                    "team_a": 2,
                    "team_h_score": None,
                    "team_a_score": None,
                    "team_h_difficulty": 3,
                    "team_a_difficulty": 4,
                    "kickoff_time": "2026-08-16T14:00:00Z",
                    "finished": False,
                }
            ]
            ingestion.ingest_fixtures()

        fixture = Fixture.objects.get(id=501)
        self.assertEqual(fixture.team_h_id, 1)
        self.assertEqual(fixture.team_a_id, 2)
        self.assertFalse(fixture.finished)
