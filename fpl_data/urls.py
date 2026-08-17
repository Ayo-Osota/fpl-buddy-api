from django.urls import path

from . import views

urlpatterns = [
    path("me/squad/", views.squad, name="squad"),
    path("me/history/", views.history, name="history"),
    path("me/budget/", views.budget, name="budget"),
    # suggest-best-squad: read-only, session-free suggestion endpoints.
    path("players/scores/", views.player_scores, name="player_scores"),
    path("players/shortlist/", views.shortlist, name="shortlist"),
    path("squads/", views.suggested_squads, name="suggested_squads"),
]
