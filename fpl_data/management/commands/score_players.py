from django.core.management.base import BaseCommand, CommandError

from fpl_data.persistence import persist_scoring_run
from fpl_data.scoring.engine import score_player_pool
from fpl_data.scoring.fixtures import (
    current_season_start_year_from_fixtures,
    next_event_number,
)
from fpl_data.strategies import STRATEGIES, get_strategy


class Command(BaseCommand):
    help = (
        "Score the current player pool and persist a ScoringRun (with "
        "per-player component scores) for one or all configured "
        "strategies. Reads only already-ingested data - run "
        "ingest_fpl_data first; this command makes no FPL API requests."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--strategy",
            help="Score only this strategy (default: every configured strategy).",
        )

    def handle(self, *args, **options):
        try:
            season_start_year = current_season_start_year_from_fixtures()
        except ValueError as exc:
            raise CommandError(str(exc))

        next_event = next_event_number()

        strategy_names = (
            [options["strategy"]] if options["strategy"] else list(STRATEGIES)
        )

        for name in strategy_names:
            strategy = get_strategy(name)
            scored_players = list(
                score_player_pool(strategy, season_start_year, next_event)
            )
            run = persist_scoring_run(strategy, season_start_year, scored_players)
            self.stdout.write(
                self.style.SUCCESS(
                    f"{name}: scored {len(scored_players)} players "
                    f"(run #{run.id}, horizon={strategy.horizon}, "
                    f"next_event={next_event})"
                )
            )
