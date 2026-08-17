import json

from django.http import JsonResponse
from django.utils import timezone

from fpl_data.fpl_client import FplApiError, client

from .models import User


def connect(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        body = {}
    raw_team_id = body.get("fpl_team_id") or request.POST.get("fpl_team_id")

    if raw_team_id is None:
        return JsonResponse({"error": "fpl_team_id is required"}, status=400)

    try:
        fpl_team_id = int(raw_team_id)
        if fpl_team_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "fpl_team_id must be a positive integer"}, status=400
        )

    try:
        is_valid = client.validate_team_id(fpl_team_id)
    except FplApiError:
        return JsonResponse(
            {"error": "could not verify Team ID with FPL right now"}, status=502
        )

    if not is_valid:
        return JsonResponse(
            {"error": "no FPL entry found for that Team ID"}, status=404
        )

    # find-or-create keyed on the unique fpl_team_id - see design.md Decision 2
    user, _ = User.objects.get_or_create(fpl_team_id=fpl_team_id)
    user.last_seen_at = timezone.now()
    user.save(update_fields=["last_seen_at"])

    request.session["user_id"] = str(user.user_id)

    return JsonResponse({"user_id": str(user.user_id), "fpl_team_id": user.fpl_team_id})
