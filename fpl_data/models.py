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
