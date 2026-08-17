"""
Single point of contact with FPL's public per-entry API - see openspec
design.md Decision 5. No other code in this service should call FPL
directly, so pacing/backoff/shape-drift detection stay in one place.
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

MIN_REQUEST_INTERVAL_SECONDS = 1.0
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0

# Fields this service actually relies on, per resource type - used only to
# detect FPL API shape drift (log a warning). The full raw payload is still
# cached/returned as-is (design.md Decision 4); this is not a schema filter.
EXPECTED_FIELDS = {
    "summary": [
        "id",
        "name",
        "player_first_name",
        "player_last_name",
        "last_deadline_bank",
        "last_deadline_value",
        "current_event",
    ],
    "picks": ["picks", "entry_history"],
    "history": ["current", "past", "chips"],
    # suggest-best-squad: global (non-per-entry) resources.
    "bootstrap_static": ["events", "teams", "elements"],
    "element_summary": ["fixtures", "history", "history_past"],
    "fixtures": ["id", "event", "team_h", "team_a"],
}


class FplApiError(Exception):
    """Raised when FPL's API returns a non-2xx, non-404 status after retries."""


class FplEntryNotFoundError(Exception):
    """Raised when a Team ID does not correspond to a real FPL entry."""


class FplClient:
    def __init__(self):
        self.base_url = settings.FPL_API_BASE_URL
        # Instance-level, but this class is used as a module-level singleton
        # (see `client` below) so pacing is enforced across requests, not
        # reset per-call.
        self._last_request_at = 0.0

    def _pace(self):
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
            time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)

    def _check_expected_fields(self, data, resource_type, path):
        # `fixtures` returns a JSON array rather than an object - check
        # shape against the first element, since an empty list has nothing
        # to drift and isn't itself a shape problem.
        if isinstance(data, list):
            if not data:
                return
            data = data[0]

        for field in EXPECTED_FIELDS.get(resource_type, []):
            if field not in data:
                logger.warning(
                    "FPL API response for %s is missing expected field %r "
                    "- FPL may have changed its response shape.",
                    path,
                    field,
                )

    def _get(self, path, expected_key=None):
        url = f"{self.base_url}{path}"
        backoff = INITIAL_BACKOFF_SECONDS
        last_exc = None

        for attempt in range(1, MAX_RETRIES + 1):
            self._pace()
            self._last_request_at = time.monotonic()
            try:
                response = requests.get(url, timeout=10)
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(
                    "FPL API request failed (attempt %d/%d) for %s: %s",
                    attempt,
                    MAX_RETRIES,
                    path,
                    exc,
                )
            else:
                if response.status_code == 404:
                    raise FplEntryNotFoundError(f"No FPL entry found at {path}")
                if response.status_code == 200:
                    data = response.json()
                    if expected_key:
                        self._check_expected_fields(data, expected_key, path)
                    return data
                last_exc = FplApiError(
                    f"FPL API returned {response.status_code} for {path}"
                )
                logger.warning(
                    "FPL API error (attempt %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    last_exc,
                )

            if attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2

        raise last_exc or FplApiError(f"FPL API request failed for {path}")

    def get_entry_summary(self, team_id):
        return self._get(f"/entry/{team_id}/", expected_key="summary")

    def get_entry_picks(self, team_id, gw):
        return self._get(f"/entry/{team_id}/event/{gw}/picks/", expected_key="picks")

    def get_entry_history(self, team_id):
        return self._get(f"/entry/{team_id}/history/", expected_key="history")

    def get_bootstrap_static(self):
        """Global player pool, teams, and gameweek (event) list."""
        return self._get("/bootstrap-static/", expected_key="bootstrap_static")

    def get_fixtures(self):
        """Global fixture list for the season."""
        return self._get("/fixtures/", expected_key="fixtures")

    def get_element_summary(self, player_id):
        """
        Per-player current-season history, past-season history, and
        upcoming fixtures. Despite the per-player URL shape, this is a
        *global* resource (any player, not a connected user's own entry) -
        paced through the same client as everything else per the module
        docstring.
        """
        return self._get(f"/element-summary/{player_id}/", expected_key="element_summary")

    def validate_team_id(self, team_id):
        try:
            self.get_entry_summary(team_id)
        except FplEntryNotFoundError:
            return False
        return True


# Module-level singleton so request pacing is enforced process-wide, not
# reset every time a view is called.
client = FplClient()
