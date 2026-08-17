from django.core.management.base import BaseCommand

from fpl_data.ingestion import run_full_ingestion


class Command(BaseCommand):
    help = (
        "Ingest global FPL data (bootstrap-static, fixtures, per-player "
        "summaries) into Postgres. Player pool/teams/fixtures always "
        "refresh in full; per-player summaries only refresh when stale "
        "unless --force-refresh is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force-refresh",
            action="store_true",
            help="Re-fetch every available player's summary, ignoring freshness.",
        )

    def handle(self, *args, **options):
        result = run_full_ingestion(force_refresh=options["force_refresh"])

        self.stdout.write(
            f"Season: {result['season_start_year']}/{result['season_start_year'] + 1 - 2000}"
        )
        self.stdout.write(f"Teams: {result['teams']}")
        self.stdout.write(f"Players in pool: {result['players_total']}")
        self.stdout.write(
            f"Skipped (unavailable): {result['players_skipped_unavailable']}"
        )
        self.stdout.write(
            self.style.SUCCESS(f"Summaries fetched: {result['players_fetched']}")
        )
        if result["players_failed"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Failed to fetch {len(result['players_failed'])} player(s): "
                    f"{result['players_failed']}"
                )
            )
