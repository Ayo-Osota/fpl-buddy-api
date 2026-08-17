import uuid

from django.db import models


class User(models.Model):
    """
    Identified by a public FPL Team ID, not a credential - see openspec
    design.md Decision 2. `email`/`password_hash` are intentionally absent;
    `email` is added nullable here as the reserved slot for a future signup
    upgrade on the same row (Decision 2), no `password_hash` field yet since
    no auth mechanism to hash for exists in this phase.
    """

    user_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fpl_team_id = models.PositiveIntegerField(unique=True)
    email = models.EmailField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"User(fpl_team_id={self.fpl_team_id})"
