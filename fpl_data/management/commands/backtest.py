from django.core.management.base import BaseCommand, CommandError

from fpl_data.backtesting import ablatable_factors, run_backtest
from fpl_data.scoring.fixtures import current_season_start_year_from_fixtures
from fpl_data.strategies import STRATEGIES


class Command(BaseCommand):
    help = (
        "Replay a range of past gameweeks with the scoring/selection "
        "pipeline, comparing realized points against random/template/"
        "price-only baselines and reporting captain accuracy. Pass "
        "--ablate to zero one scoring factor and measure its contribution."
    )

    def add_arguments(self, parser):
        parser.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
        parser.add_argument("--from-event", type=int, required=True)
        parser.add_argument("--to-event", type=int, required=True)
        parser.add_argument(
            "--season-start-year",
            type=int,
            help="Defaults to the season derived from ingested fixtures.",
        )
        parser.add_argument(
            "--ablate",
            choices=ablatable_factors(),
            help="Zero this factor's weight before running the backtest.",
        )

    def handle(self, *args, **options):
        if options["from_event"] > options["to_event"]:
            raise CommandError("--from-event must be <= --to-event")

        season_start_year = options["season_start_year"]
        if season_start_year is None:
            try:
                season_start_year = current_season_start_year_from_fixtures()
            except ValueError as exc:
                raise CommandError(str(exc))

        replay_events = range(options["from_event"], options["to_event"] + 1)
        results = run_backtest(
            options["strategy"],
            replay_events,
            season_start_year,
            ablate_factor=options["ablate"],
        )

        if not results:
            self.stdout.write(
                self.style.WARNING("No feasible replay points in that range.")
            )
            return

        for r in results:
            b = r.baseline_realized_points
            self.stdout.write(
                f"GW{r.replay_event}: squad={r.squad_realized_points} "
                f"random={b['random']} template={b['template']} price_only={b['price_only']} "
                f"captain(season/next_gw/differential)="
                f"{r.captain_outscored_median['season']}/"
                f"{r.captain_outscored_median['next_gw']}/"
                f"{r.captain_outscored_median['differential']}"
            )

        avg_squad = sum(r.squad_realized_points for r in results) / len(results)
        avg_template = sum(r.baseline_realized_points["template"] for r in results) / len(
            results
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Averages over {len(results)} replay point(s): "
                f"squad={avg_squad:.1f}, template_baseline={avg_template:.1f}"
            )
        )
