from functools import wraps

from django.http import JsonResponse

from .models import User


def get_current_user(request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        return User.objects.get(user_id=user_id)
    except User.DoesNotExist:
        return None


def require_connected_user(view_fn):
    """View decorator enforcing spec `fpl-team-connection`'s "No session
    requires reconnect" scenario."""

    @wraps(view_fn)
    def wrapper(request, *args, **kwargs):
        user = get_current_user(request)
        if user is None:
            return JsonResponse(
                {"error": "not connected - submit your FPL Team ID to /connect/"},
                status=401,
            )
        request.fpl_user = user
        return view_fn(request, *args, **kwargs)

    return wrapper
