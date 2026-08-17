from django.core.management.base import BaseCommand

from fpl_data.models import ScoringRun
from fpl_data.optimization import InfeasibleStrategyError, select_squad
from fpl_data.persistence import persist_suggested_squad, scored_players_from_run
from fpl_data.selection import select_captains, select_formation_and_starters
from fpl_data.strategies import STRATEGIES, get_strategy


class Command(BaseCommand):
    help = (
        "Build a suggested squad for one or all configured strategies, "
        "using the latest stored ScoringRun for each - see "
        "'Squad building runs without re-scoring'. Run score_players "
        "first. An infeasible strategy is reported and skipped without "
        "blocking the others."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--strategy",
            help="Build only this strategy's squad (default: every configured strategy).",
        )

    def handle(self, *args, **options):
        strategy_names = (
            [options["strategy"]] if options["strategy"] else list(STRATEGIES)
        )

        for name in strategy_names:
            strategy = get_strategy(name)
            run = (
                ScoringRun.objects.filter(strategy_name=name)
                .order_by("-created_at")
                .first()
            )
            if run is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"{name}: no scoring run found - run score_players first"
                    )
                )
                continue

            scored_players = scored_players_from_run(run)
            try:
                squad = select_squad(scored_players, strategy)
            except InfeasibleStrategyError:
                self.stdout.write(
                    self.style.WARNING(f"{name}: infeasible - no legal squad exists")
                )
                continue

            xi_result = select_formation_and_starters(squad)
            captains = select_captains(xi_result.starters)
            total_price = sum(sp.player.now_cost for sp in squad)

            suggested = persist_suggested_squad(
                run, strategy, xi_result, captains, total_price
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{name}: {xi_result.formation}, "
                    f"£{total_price / 10:.1f}m (squad #{suggested.id})"
                )
            )
