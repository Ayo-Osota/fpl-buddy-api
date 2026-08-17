from collections import defaultdict

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from accounts.session import require_connected_user

from .fpl_client import FplApiError, FplEntryNotFoundError
from .models import PlayerScore, ScoringRun, SuggestedSquad, SuggestedSquadPlayer
from .services import (
    get_entry_history_cached,
    get_entry_picks_cached,
    get_entry_summary_cached,
)
from .strategies import STRATEGIES


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


# --- suggest-best-squad: read-only suggestion endpoints -------------------
# These serve stored ScoringRun/PlayerScore/SuggestedSquad rows only - they
# never contact the FPL API or run the optimizer (see squad-suggestion-api's
# "Read-Only Suggestion Endpoints") and require no connected session (see
# "Existing Per-Entry Endpoints Unchanged" / "Suggestion endpoints require
# no session").


def _player_brief(player):
    return {
        "id": player.id,
        "web_name": player.web_name,
        "team": player.team.short_name,
        "position": player.get_element_type_display(),
        "price_tenths": player.now_cost,
    }


def _latest_run(strategy_name):
    return (
        ScoringRun.objects.filter(strategy_name=strategy_name)
        .order_by("-created_at")
        .first()
    )


@require_GET
def player_scores(request):
    strategy_name = request.GET.get("strategy", "balanced")
    run = _latest_run(strategy_name)
    if run is None:
        return JsonResponse(
            {"available": False, "message": "No scoring run yet for this strategy."}
        )

    scores = (
        PlayerScore.objects.filter(scoring_run=run)
        .select_related("player", "player__team")
        .order_by("-total_score")
    )
    return JsonResponse(
        {
            "available": True,
            "strategy": strategy_name,
            "run_id": run.id,
            "generated_at": run.created_at.isoformat(),
            "players": [
                {
                    **_player_brief(ps.player),
                    "total_score": ps.total_score,
                    "next_gw_score": ps.next_gw_score,
                    "expected_component": ps.expected_component,
                    "realized_component": ps.realized_component,
                    "regression_signal": ps.regression_signal,
                    "fixture_component": ps.fixture_component,
                    "setpiece_component": ps.setpiece_component,
                    "ownership_component": ps.ownership_component,
                    "rotation_component": ps.rotation_component,
                    "has_history": ps.has_history,
                }
                for ps in scores
            ],
        }
    )


@require_GET
def shortlist(request):
    strategy_name = request.GET.get("strategy", "balanced")
    top_n = int(request.GET.get("top_n", 15))
    run = _latest_run(strategy_name)
    if run is None:
        return JsonResponse(
            {"available": False, "message": "No scoring run yet for this strategy."}
        )

    scores = PlayerScore.objects.filter(scoring_run=run).select_related(
        "player", "player__team"
    )
    by_position = defaultdict(list)
    for ps in scores:
        by_position[ps.player.element_type].append(ps)

    result = {}
    for position, entries in by_position.items():
        entries.sort(key=lambda ps: ps.total_score, reverse=True)
        result[str(position)] = [
            {**_player_brief(ps.player), "total_score": ps.total_score}
            for ps in entries[:top_n]
        ]

    return JsonResponse(
        {
            "available": True,
            "strategy": strategy_name,
            "run_id": run.id,
            "shortlist": result,
        }
    )


def _serialize_squad(squad):
    members = SuggestedSquadPlayer.objects.filter(squad=squad).select_related(
        "player", "player__team"
    )
    starters = [m for m in members if m.is_starter]
    bench_gk = next(m for m in members if m.is_bench_goalkeeper)
    bench_outfield = sorted(
        (m for m in members if not m.is_starter and not m.is_bench_goalkeeper),
        key=lambda m: m.bench_rank,
    )

    differential = None
    if squad.differential_captain_id is not None:
        differential = {
            "captain": _player_brief(squad.differential_captain),
            "vice_captain": _player_brief(squad.differential_vice_captain),
        }

    return {
        "strategy": squad.strategy_name,
        "formation": squad.formation,
        "total_price_tenths": squad.total_price,
        "starters": [_player_brief(m.player) for m in starters],
        "bench": {
            "goalkeeper": _player_brief(bench_gk.player),
            "outfield": [_player_brief(m.player) for m in bench_outfield],
        },
        "captains": {
            "season": {
                "captain": _player_brief(squad.season_captain),
                "vice_captain": _player_brief(squad.season_vice_captain),
            },
            "next_gw": {
                "captain": _player_brief(squad.next_gw_captain),
                "vice_captain": _player_brief(squad.next_gw_vice_captain),
            },
            "differential": differential,
        },
    }


@require_GET
def suggested_squads(request):
    strategy_param = request.GET.get("strategy")
    strategy_names = [strategy_param] if strategy_param else list(STRATEGIES)

    squads_out = []
    for name in strategy_names:
        squad = (
            SuggestedSquad.objects.filter(strategy_name=name)
            .order_by("-created_at")
            .select_related(
                "season_captain",
                "season_vice_captain",
                "next_gw_captain",
                "next_gw_vice_captain",
                "differential_captain",
                "differential_vice_captain",
            )
            .first()
        )
        if squad is not None:
            squads_out.append(_serialize_squad(squad))

    if not squads_out:
        return JsonResponse({"available": False, "message": "No suggested squads yet."})

    return JsonResponse({"available": True, "squads": squads_out})
