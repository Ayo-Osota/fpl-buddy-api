from django.urls import path

from . import views

urlpatterns = [
    path("me/squad/", views.squad, name="squad"),
    path("me/history/", views.history, name="history"),
    path("me/budget/", views.budget, name="budget"),
]
