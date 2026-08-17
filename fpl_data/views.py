from django.http import JsonResponse

from accounts.session import require_connected_user

from .fpl_client import FplApiError, FplEntryNotFoundError
from .services import (
    get_entry_history_cached,
    get_entry_picks_cached,
    get_entry_summary_cached,
)


@require_connected_user
def squad(request):
    team_id = request.fpl_user.fpl_team_id
    try:
        summary = get_entry_summary_cached(team_id)
    except FplApiError:
        return JsonResponse({"error": "could not reach FPL right now"}, status=502)

    gw = summary.get("current_event")
    if gw is None:
        # Preseason / between-deadline case: FPL hasn't computed a "current"
        # gameweek yet, so there's no locked squad to show.
        return JsonResponse(
            {
                "picks_available": False,
                "gw": None,
                "message": "No gameweek squad available yet this season.",
            }
        )

    try:
        picks_payload = get_entry_picks_cached(team_id, gw)
    except FplEntryNotFoundError:
        return JsonResponse(
            {
                "picks_available": False,
                "gw": gw,
                "message": "No locked squad for this gameweek yet.",
            }
        )
    except FplApiError:
        return JsonResponse({"error": "could not reach FPL right now"}, status=502)

    picks = picks_payload.get("picks", [])
    starters = [p for p in picks if p.get("position", 99) <= 11]
    bench = [p for p in picks if p.get("position", 0) > 11]
    captain = next((p["element"] for p in picks if p.get("is_captain")), None)
    vice_captain = next(
        (p["element"] for p in picks if p.get("is_vice_captain")), None
    )

    return JsonResponse(
        {
            "picks_available": True,
            "gw": gw,
            "starters": starters,
            "bench": bench,
            "captain": captain,
            "vice_captain": vice_captain,
        }
    )


@require_connected_user
def history(request):
    team_id = request.fpl_user.fpl_team_id
    try:
        payload = get_entry_history_cached(team_id)
    except FplApiError:
        return JsonResponse({"error": "could not reach FPL right now"}, status=502)

    gw_history = [
        {
            "gw": gw["event"],
            "points": gw["points"],
            "transfers": gw["event_transfers"],
            "transfer_cost": gw["event_transfers_cost"],
        }
        for gw in payload.get("current", [])
    ]
    chips = [{"name": c["name"], "gw": c["event"]} for c in payload.get("chips", [])]

    return JsonResponse({"gw_history": gw_history, "chips": chips})


@require_connected_user
def budget(request):
    team_id = request.fpl_user.fpl_team_id
    try:
        summary = get_entry_summary_cached(team_id)
    except FplApiError:
        return JsonResponse({"error": "could not reach FPL right now"}, status=502)

    # FPL returns bank/value as tenths of a million (e.g. 1005 == £100.5m).
    return JsonResponse(
        {
            "bank_tenths": summary.get("last_deadline_bank"),
            "squad_value_tenths": summary.get("last_deadline_value"),
        }
    )
