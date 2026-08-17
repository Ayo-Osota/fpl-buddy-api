from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .fpl_client import client
from .models import FplDataCache


def _is_fresh(cache_entry):
    if cache_entry is None:
        return False
    age = timezone.now() - cache_entry.fetched_at
    return age < timedelta(seconds=settings.FPL_CACHE_TTL_SECONDS)


def get_cached_or_fetch(fpl_team_id, resource_type, gw, fetch_fn):
    """
    Cache-first read per design.md Decision 4/5 and the
    fpl-team-data-retrieval spec's "Cache-First Reads With TTL": serve the
    cached payload if it's within FPL_CACHE_TTL_SECONDS, otherwise call
    fetch_fn(), write the result through to the cache, and return it.

    Always goes through update_or_create rather than a raw insert - see the
    note on FplDataCache about Postgres allowing multiple NULLs under a
    unique constraint (gw is null for non-gameweek-specific resources).
    """
    entry = FplDataCache.objects.filter(
        fpl_team_id=fpl_team_id, resource_type=resource_type, gw=gw
    ).first()
    if _is_fresh(entry):
        return entry.payload

    payload = fetch_fn()
    FplDataCache.objects.update_or_create(
        fpl_team_id=fpl_team_id,
        resource_type=resource_type,
        gw=gw,
        defaults={"payload": payload},
    )
    return payload


def get_entry_summary_cached(fpl_team_id):
    return get_cached_or_fetch(
        fpl_team_id, "summary", None, lambda: client.get_entry_summary(fpl_team_id)
    )


def get_entry_history_cached(fpl_team_id):
    return get_cached_or_fetch(
        fpl_team_id, "history", None, lambda: client.get_entry_history(fpl_team_id)
    )


def get_entry_picks_cached(fpl_team_id, gw):
    return get_cached_or_fetch(
        fpl_team_id, "picks", gw, lambda: client.get_entry_picks(fpl_team_id, gw)
    )
