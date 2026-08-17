from django.db import models


class FplDataCache(models.Model):
    """
    Raw-payload cache for FPL's per-entry API responses - see design.md
    Decision 4 (JSONB blob, not a normalized schema, so FPL's habit of
    renaming fields doesn't require a migration to absorb).

    resource_type is one of: "summary" (/api/entry/{id}/), "picks"
    (/api/entry/{id}/event/{gw}/picks/), "history" (/api/entry/{id}/history/).
    `gw` is null for resource types that aren't gameweek-specific (summary,
    history). Postgres allows multiple NULLs under a unique constraint, so
    the constraint below is a best-effort guard, not the sole correctness
    mechanism - all writes must go through update_or_create (see
    fpl_data.services) rather than raw inserts, since Django's ORM lookup
    correctly matches `gw=None` as `IS NULL` even though the DB constraint
    alone wouldn't stop duplicate NULL rows.
    """

    fpl_team_id = models.PositiveIntegerField()
    resource_type = models.CharField(max_length=32)
    gw = models.PositiveIntegerField(null=True, blank=True)
    payload = models.JSONField()
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["fpl_team_id", "resource_type", "gw"],
                name="unique_fpl_data_cache_entry",
            )
        ]

    def __str__(self):
        return f"FplDataCache({self.fpl_team_id}, {self.resource_type}, gw={self.gw})"


class Team(models.Model):
    """
    One row per Premier League club, from bootstrap-static's `teams` array.
    `id` is FPL's own team id (used as the primary key rather than a
    surrogate) so Player.team and Fixture.team_h/team_a can reference it
    directly without an id-mapping layer - see suggest-best-squad design.md
    Decision 2 (global data is normalized, not raw JSONB, because it's
    queried/filtered/joined rather than replayed).
    """

    id = models.PositiveIntegerField(primary_key=True)
    code = models.PositiveIntegerField()
    name = models.CharField(max_length=64)
    short_name = models.CharField(max_length=8)
    strength = models.PositiveSmallIntegerField(default=0)
    strength_overall_home = models.PositiveSmallIntegerField(default=0)
    strength_overall_away = models.PositiveSmallIntegerField(default=0)
    strength_attack_home = models.PositiveSmallIntegerField(default=0)
    strength_attack_away = models.PositiveSmallIntegerField(default=0)
    strength_defence_home = models.PositiveSmallIntegerField(default=0)
    strength_defence_away = models.PositiveSmallIntegerField(default=0)
    fetched_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.short_name


class Player(models.Model):
    """
    One row per FPL player ("element"), from bootstrap-static's `elements`
    array. Holds every field the scoring engine (fpl_data.scoring) actually
    reads - see player-performance-scoring spec. Fields bootstrap-static
    parses but the prototype never scored with (penalties_order,
    selected_by_percent, starts_per_90, the *_rank fields, ...) are included
    here specifically so suggest-best-squad's scoring engine can use them.
    """

    class Position(models.IntegerChoices):
        GOALKEEPER = 1, "Goalkeeper"
        DEFENDER = 2, "Defender"
        MIDFIELDER = 3, "Midfielder"
        FORWARD = 4, "Forward"

    id = models.PositiveIntegerField(primary_key=True)
    code = models.PositiveIntegerField()
    first_name = models.CharField(max_length=128)
    second_name = models.CharField(max_length=128)
    web_name = models.CharField(max_length=128)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="players")
    element_type = models.PositiveSmallIntegerField(choices=Position.choices)

    # Price, in tenths of a million (FPL's native unit - e.g. 125 == £12.5m),
    # matching the existing budget view's convention for last_deadline_*.
    now_cost = models.PositiveIntegerField()

    # Availability - see "Player Availability from Status and Fitness".
    status = models.CharField(max_length=1, default="a")
    chance_of_playing_next_round = models.PositiveSmallIntegerField(
        null=True, blank=True
    )
    news = models.TextField(blank=True, default="")

    # Ownership - see "Ownership Contributes to Scoring".
    selected_by_percent = models.FloatField(default=0.0)

    # Season-to-date totals and per-90 rates.
    total_points = models.IntegerField(default=0)
    form = models.FloatField(default=0.0)
    points_per_game = models.FloatField(default=0.0)
    minutes = models.PositiveIntegerField(default=0)
    starts = models.PositiveIntegerField(default=0)
    starts_per_90 = models.FloatField(default=0.0)

    goals_scored = models.PositiveIntegerField(default=0)
    assists = models.PositiveIntegerField(default=0)
    clean_sheets = models.PositiveIntegerField(default=0)
    goals_conceded = models.PositiveIntegerField(default=0)
    own_goals = models.PositiveIntegerField(default=0)
    penalties_saved = models.PositiveIntegerField(default=0)
    penalties_missed = models.PositiveIntegerField(default=0)
    saves = models.PositiveIntegerField(default=0)
    bonus = models.PositiveIntegerField(default=0)

    yellow_cards = models.PositiveIntegerField(default=0)
    red_cards = models.PositiveIntegerField(default=0)

    influence = models.FloatField(default=0.0)
    creativity = models.FloatField(default=0.0)
    threat = models.FloatField(default=0.0)
    ict_index = models.FloatField(default=0.0)
    influence_rank = models.PositiveIntegerField(null=True, blank=True)
    creativity_rank = models.PositiveIntegerField(null=True, blank=True)
    threat_rank = models.PositiveIntegerField(null=True, blank=True)
    ict_index_rank = models.PositiveIntegerField(null=True, blank=True)

    expected_goals = models.FloatField(default=0.0)
    expected_assists = models.FloatField(default=0.0)
    expected_goal_involvements = models.FloatField(default=0.0)
    expected_goals_conceded = models.FloatField(default=0.0)
    expected_goals_per_90 = models.FloatField(default=0.0)
    expected_assists_per_90 = models.FloatField(default=0.0)
    saves_per_90 = models.FloatField(default=0.0)
    clean_sheets_per_90 = models.FloatField(default=0.0)

    defensive_contribution = models.FloatField(default=0.0)
    defensive_contribution_per_90 = models.FloatField(default=0.0)

    # Set-piece duty - see "Set-Piece Duty Contributes to Scoring". Null
    # means "not a listed taker", distinct from an order of e.g. 1.
    penalties_order = models.PositiveSmallIntegerField(null=True, blank=True)
    direct_freekicks_order = models.PositiveSmallIntegerField(null=True, blank=True)
    corners_and_indirect_freekicks_order = models.PositiveSmallIntegerField(
        null=True, blank=True
    )

    fetched_at = models.DateTimeField(auto_now=True)
    # Set when this player's element-summary has been fetched at least once
    # this run (as opposed to only ever appearing in bootstrap-static) - lets
    # scoring distinguish "no history because never fetched" from "no
    # history because the season/player genuinely has none".
    summary_fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["element_type"])]

    def __str__(self):
        return self.web_name

    @property
    def position_name(self):
        return self.Position(self.element_type).label


class PlayerGameweekHistory(models.Model):
    """
    One row per (player, gameweek) for the *current* season, from
    element-summary's `history` array.
    """

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="gameweek_history"
    )
    round = models.PositiveSmallIntegerField()
    opponent_team = models.ForeignKey(
        Team, on_delete=models.SET_NULL, null=True, blank=True
    )
    was_home = models.BooleanField(default=False)
    kickoff_time = models.DateTimeField(null=True, blank=True)

    total_points = models.IntegerField(default=0)
    minutes = models.PositiveIntegerField(default=0)
    goals_scored = models.PositiveIntegerField(default=0)
    assists = models.PositiveIntegerField(default=0)
    clean_sheets = models.PositiveIntegerField(default=0)
    goals_conceded = models.PositiveIntegerField(default=0)
    own_goals = models.PositiveIntegerField(default=0)
    penalties_saved = models.PositiveIntegerField(default=0)
    penalties_missed = models.PositiveIntegerField(default=0)
    yellow_cards = models.PositiveIntegerField(default=0)
    red_cards = models.PositiveIntegerField(default=0)
    saves = models.PositiveIntegerField(default=0)
    bonus = models.PositiveIntegerField(default=0)

    influence = models.FloatField(default=0.0)
    creativity = models.FloatField(default=0.0)
    threat = models.FloatField(default=0.0)
    ict_index = models.FloatField(default=0.0)

    expected_goals = models.FloatField(default=0.0)
    expected_assists = models.FloatField(default=0.0)
    expected_goal_involvements = models.FloatField(default=0.0)
    expected_goals_conceded = models.FloatField(default=0.0)
    defensive_contribution = models.FloatField(default=0.0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "round"], name="unique_player_gameweek_history"
            )
        ]

    def __str__(self):
        return f"{self.player.web_name} GW{self.round}"


class PlayerSeasonHistory(models.Model):
    """
    One row per (player, past season), from element-summary's
    `history_past` array. `defensive_goals_conceded_per_90` was only added
    to the FPL API for the 2025/26 season onward - older rows leave it at
    the default 0, which is what the prototype's calculate_performance
    already treats as "no bonus, never a penalty" (see
    player-performance-scoring's existing docstring on services.py... no,
    on fpl_data/models.py FplDataCache - the same "only ever adds a bonus"
    reasoning applies here).
    """

    player = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="season_history"
    )
    season_name = models.CharField(max_length=16)

    start_cost = models.PositiveIntegerField(default=0)
    end_cost = models.PositiveIntegerField(default=0)
    total_points = models.IntegerField(default=0)
    minutes = models.PositiveIntegerField(default=0)
    goals_scored = models.PositiveIntegerField(default=0)
    assists = models.PositiveIntegerField(default=0)
    clean_sheets = models.PositiveIntegerField(default=0)
    goals_conceded = models.PositiveIntegerField(default=0)
    own_goals = models.PositiveIntegerField(default=0)
    penalties_saved = models.PositiveIntegerField(default=0)
    penalties_missed = models.PositiveIntegerField(default=0)
    yellow_cards = models.PositiveIntegerField(default=0)
    red_cards = models.PositiveIntegerField(default=0)
    saves = models.PositiveIntegerField(default=0)
    bonus = models.PositiveIntegerField(default=0)

    influence = models.FloatField(default=0.0)
    creativity = models.FloatField(default=0.0)
    threat = models.FloatField(default=0.0)
    ict_index = models.FloatField(default=0.0)

    expected_goals = models.FloatField(default=0.0)
    expected_assists = models.FloatField(default=0.0)
    expected_goal_involvements = models.FloatField(default=0.0)
    expected_goals_conceded = models.FloatField(default=0.0)
    defensive_contribution_per_90 = models.FloatField(default=0.0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "season_name"],
                name="unique_player_season_history",
            )
        ]

    def __str__(self):
        return f"{self.player.web_name} {self.season_name}"


class Fixture(models.Model):
    """
    One row per fixture, from the global `fixtures` endpoint. `id` is FPL's
    own fixture id.
    """

    id = models.PositiveIntegerField(primary_key=True)
    event = models.PositiveSmallIntegerField(null=True, blank=True)
    team_h = models.ForeignKey(
        Team, on_delete=models.CASCADE, related_name="home_fixtures"
    )
    team_a = models.ForeignKey(
        Team, on_delete=models.CASCADE, related_name="away_fixtures"
    )
    team_h_score = models.PositiveSmallIntegerField(null=True, blank=True)
    team_a_score = models.PositiveSmallIntegerField(null=True, blank=True)
    team_h_difficulty = models.PositiveSmallIntegerField(null=True, blank=True)
    team_a_difficulty = models.PositiveSmallIntegerField(null=True, blank=True)
    kickoff_time = models.DateTimeField(null=True, blank=True)
    finished = models.BooleanField(default=False)
    fetched_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Fixture({self.team_h_id} v {self.team_a_id}, gw={self.event})"


class ScoringRun(models.Model):
    """
    One row per scoring pass (see suggest-best-squad's "Scoring Runs Are
    Persisted with Component Scores"). `weights` is the resolved strategy
    configuration used for this run - kept as JSON (rather than only a
    strategy name) so a run remains reproducible/inspectable even if the
    named strategy's coefficients change in code later.
    """

    strategy_name = models.CharField(max_length=64)
    weights = models.JSONField()
    season_start_year = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ScoringRun({self.strategy_name}, {self.created_at:%Y-%m-%d %H:%M})"


class PlayerScore(models.Model):
    """
    One row per (scoring run, player): the final score plus every component
    that produced it, so "why did this player drop out of the shortlist"
    is answerable without recomputing - see "Scoring Runs Are Persisted
    with Component Scores" and "Realized Output Separated from Expected
    Output".
    """

    scoring_run = models.ForeignKey(
        ScoringRun, on_delete=models.CASCADE, related_name="player_scores"
    )
    player = models.ForeignKey(Player, on_delete=models.CASCADE)

    total_score = models.FloatField()
    next_gw_score = models.FloatField()

    expected_component = models.FloatField()
    realized_component = models.FloatField()
    regression_signal = models.FloatField()
    fixture_component = models.FloatField()
    setpiece_component = models.FloatField()
    ownership_component = models.FloatField()
    rotation_component = models.FloatField()

    availability_multiplier = models.FloatField()
    discipline_multiplier = models.FloatField()

    # See "Players Without Any History Are Identifiable" - distinguishes a
    # genuinely low score from "we have no data to score this player on".
    has_history = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scoring_run", "player"], name="unique_scoring_run_player"
            )
        ]
        indexes = [models.Index(fields=["scoring_run", "total_score"])]

    def __str__(self):
        return f"PlayerScore({self.player.web_name}, {self.total_score:.2f})"


class SuggestedSquad(models.Model):
    """
    One row per (scoring run, strategy): the selected 15, formation, and
    the three captain recommendations - see starting-xi-selection and
    squad-suggestion-api's "Squad Response Contains Full Composition".
    """

    scoring_run = models.ForeignKey(
        ScoringRun, on_delete=models.CASCADE, related_name="squads"
    )
    strategy_name = models.CharField(max_length=64)
    formation = models.CharField(max_length=8)
    total_price = models.PositiveIntegerField()  # tenths of a million

    season_captain = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="+"
    )
    season_vice_captain = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="+"
    )
    next_gw_captain = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="+"
    )
    next_gw_vice_captain = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="+"
    )
    # Null when no starter falls below the differential ownership threshold
    # - see "No eligible differential captain".
    differential_captain = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="+", null=True, blank=True
    )
    differential_vice_captain = models.ForeignKey(
        Player, on_delete=models.CASCADE, related_name="+", null=True, blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"SuggestedSquad({self.strategy_name}, {self.formation})"


class SuggestedSquadPlayer(models.Model):
    """
    One row per player in a SuggestedSquad - membership plus starter/bench
    placement. Bench order matters because FPL autosubs follow it (see
    "Bench Is Ranked"): `is_bench_goalkeeper` marks the GK sub's own slot,
    and `bench_rank` (1-3) orders the outfield subs.
    """

    squad = models.ForeignKey(
        SuggestedSquad, on_delete=models.CASCADE, related_name="players"
    )
    player = models.ForeignKey(Player, on_delete=models.CASCADE)
    is_starter = models.BooleanField(default=False)
    is_bench_goalkeeper = models.BooleanField(default=False)
    bench_rank = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["squad", "player"], name="unique_squad_player"
            )
        ]

    def __str__(self):
        return f"{self.squad}: {self.player.web_name}"
