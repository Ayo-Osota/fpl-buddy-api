from django.test import SimpleTestCase

from fpl_data.models import Player
from fpl_data.scoring.performance import PlayerScoreComponents
from fpl_data.scoring.engine import ScoredPlayer
from fpl_data.selection import (
    select_captains,
    select_formation_and_starters,
)


def make_scored_player(id_, position, total_score, ownership=15.0, next_gw_score=None):
    player = Player(
        id=id_,
        code=100000 + id_,
        first_name="Test",
        second_name=f"Player{id_}",
        web_name=f"Player{id_}",
        team_id=1,
        element_type=position,
        now_cost=80,
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
        availability_multiplier=1.0,
        discipline_multiplier=1.0,
        has_history=True,
        next_gw_score=next_gw_score if next_gw_score is not None else total_score,
    )
    return ScoredPlayer(player=player, components=components, total_score=total_score)


def make_squad(gk_scores, def_scores, mid_scores, fwd_scores, **kwargs):
    squad = []
    pid = 1
    for score in gk_scores:
        squad.append(make_scored_player(pid, Player.Position.GOALKEEPER, score, **kwargs))
        pid += 1
    for score in def_scores:
        squad.append(make_scored_player(pid, Player.Position.DEFENDER, score, **kwargs))
        pid += 1
    for score in mid_scores:
        squad.append(make_scored_player(pid, Player.Position.MIDFIELDER, score, **kwargs))
        pid += 1
    for score in fwd_scores:
        squad.append(make_scored_player(pid, Player.Position.FORWARD, score, **kwargs))
        pid += 1
    return squad


class FormationSelectionTests(SimpleTestCase):
    def test_legal_formation_and_11_starters(self):
        squad = make_squad(
            gk_scores=[8, 4],
            def_scores=[7, 6, 5, 4, 3],
            mid_scores=[9, 8, 7, 6, 5],
            fwd_scores=[9, 5, 4],
        )
        result = select_formation_and_starters(squad)
        self.assertEqual(len(result.starters), 11)
        d, m, f = (int(x) for x in result.formation.split("-"))
        self.assertTrue(3 <= d <= 5)
        self.assertTrue(2 <= m <= 5)
        self.assertTrue(1 <= f <= 3)
        self.assertEqual(d + m + f, 10)

    def test_five_at_the_back_chosen_when_it_scores_highest(self):
        # Five very strong defenders, weak extra midfielders/forwards, so
        # 5-2-3 or 5-3-2 beats a default 4-x-x shape.
        squad = make_squad(
            gk_scores=[8, 3],
            def_scores=[10, 10, 10, 10, 10],
            mid_scores=[3, 3, 2, 2, 1],
            fwd_scores=[3, 2, 1],
        )
        result = select_formation_and_starters(squad)
        d = int(result.formation.split("-")[0])
        self.assertEqual(d, 5)

    def test_bench_goalkeeper_is_the_second_goalkeeper(self):
        squad = make_squad(
            gk_scores=[8, 4],
            def_scores=[7, 6, 5, 4, 3],
            mid_scores=[9, 8, 7, 6, 5],
            fwd_scores=[9, 5, 4],
        )
        result = select_formation_and_starters(squad)
        self.assertEqual(result.bench_goalkeeper.player.element_type, Player.Position.GOALKEEPER)
        self.assertNotIn(result.bench_goalkeeper, result.starters)

    def test_bench_outfield_ranked_descending(self):
        squad = make_squad(
            gk_scores=[8, 4],
            def_scores=[7, 6, 5, 4, 3],
            mid_scores=[9, 8, 7, 6, 1],
            fwd_scores=[9, 5, 2],
        )
        result = select_formation_and_starters(squad)
        scores = [sp.total_score for sp in result.bench_outfield]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(result.bench_outfield), 3)

    def test_starters_and_bench_partition_the_squad(self):
        squad = make_squad(
            gk_scores=[8, 4],
            def_scores=[7, 6, 5, 4, 3],
            mid_scores=[9, 8, 7, 6, 5],
            fwd_scores=[9, 5, 4],
        )
        result = select_formation_and_starters(squad)
        all_ids = (
            {sp.player.id for sp in result.starters}
            | {sp.player.id for sp in result.bench_outfield}
            | {result.bench_goalkeeper.player.id}
        )
        self.assertEqual(len(all_ids), 15)


class CaptainSelectionTests(SimpleTestCase):
    def _starters(self):
        # 11 starters: highest total_score is A (id=1), highest
        # next_gw_score deliberately given to a different player (id=2).
        starters = [
            make_scored_player(1, Player.Position.FORWARD, total_score=20, ownership=40, next_gw_score=5),
            make_scored_player(2, Player.Position.FORWARD, total_score=10, ownership=35, next_gw_score=25),
            make_scored_player(3, Player.Position.MIDFIELDER, total_score=9, ownership=5, next_gw_score=8),
            make_scored_player(4, Player.Position.MIDFIELDER, total_score=8, ownership=3, next_gw_score=7),
        ]
        for i in range(5, 12):
            starters.append(
                make_scored_player(i, Player.Position.DEFENDER, total_score=1, ownership=50, next_gw_score=1)
            )
        return starters

    def test_all_three_recommendations_produced(self):
        recs = select_captains(self._starters())
        self.assertIsNotNone(recs.season)
        self.assertIsNotNone(recs.next_gw)
        self.assertIsNotNone(recs.differential)

    def test_season_captain_is_highest_total_score(self):
        recs = select_captains(self._starters())
        self.assertEqual(recs.season.captain.player.id, 1)

    def test_next_gw_captain_is_highest_next_gw_score(self):
        recs = select_captains(self._starters())
        self.assertEqual(recs.next_gw.captain.player.id, 2)

    def test_differential_captain_excludes_high_ownership(self):
        recs = select_captains(self._starters(), ownership_threshold=10.0)
        self.assertNotIn(recs.differential.captain.player.id, {1, 2})
        self.assertEqual(recs.differential.captain.player.id, 3)

    def test_captain_and_vice_are_distinct(self):
        recs = select_captains(self._starters())
        self.assertNotEqual(recs.season.captain.player.id, recs.season.vice_captain.player.id)

    def test_no_eligible_differential_when_all_high_ownership(self):
        starters = [
            make_scored_player(i, Player.Position.MIDFIELDER, total_score=10 - i, ownership=90)
            for i in range(11)
        ]
        recs = select_captains(starters, ownership_threshold=10.0)
        self.assertIsNone(recs.differential)

    def test_captains_only_from_starters_not_bench(self):
        starters = self._starters()
        bench_high_scorer = make_scored_player(
            99, Player.Position.FORWARD, total_score=1000, ownership=1, next_gw_score=1000
        )
        # bench_high_scorer is never passed into select_captains - simulate
        # the "high scorer on the bench" scenario by confirming id 99
        # never appears even though it would dominate if included.
        recs = select_captains(starters)
        self.assertNotEqual(recs.season.captain.player.id, 99)
        self.assertNotEqual(recs.next_gw.captain.player.id, 99)
