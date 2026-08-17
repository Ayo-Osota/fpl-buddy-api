"""
Django settings for the connect-fpl-team backend service.

Deliberately minimal INSTALLED_APPS/MIDDLEWARE: no django.contrib.admin,
django.contrib.auth, django.contrib.contenttypes, or django.contrib.messages.
This service's identity model (accounts.User) is keyed on a public FPL Team
ID, not a password, and django.contrib.auth is built around credential-based
login (see openspec design.md Decision 3) - pulling it in "just for the
admin site" would drag in a whole password/permissions system this project
explicitly isn't using. CsrfViewMiddleware is also left out for now since
there's no frontend yet issuing CSRF tokens (fpl-buddy-fe integration is a
future change) - revisit when that lands.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-local-dev-only")
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.sessions",
    "accounts",
    "fpl_data",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
# Reads DATABASE_URL if set (Railway's convention - see design.md Decision 7),
# otherwise falls back to discrete local DATABASE_* env vars for local dev.
_database_url = os.environ.get("DATABASE_URL")
if _database_url:
    import urllib.parse

    _parsed = urllib.parse.urlparse(_database_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _parsed.path.lstrip("/"),
            "USER": _parsed.username,
            "PASSWORD": _parsed.password,
            "HOST": _parsed.hostname,
            "PORT": _parsed.port or 5432,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DATABASE_NAME", "fpl_buddy"),
            "USER": os.environ.get("DATABASE_USER", "fpl_buddy"),
            "PASSWORD": os.environ.get("DATABASE_PASSWORD", "fpl_buddy"),
            "HOST": os.environ.get("DATABASE_HOST", "localhost"),
            "PORT": os.environ.get("DATABASE_PORT", "5432"),
        }
    }

# Sessions: DB-backed (Django's default SESSION_ENGINE) per design.md Decision 3.
SESSION_ENGINE = "django.contrib.sessions.backends.db"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# FPL API client config (see fpl_data.fpl_client)
FPL_API_BASE_URL = os.environ.get(
    "FPL_API_BASE_URL", "https://fantasy.premierleague.com/api"
)
FPL_CACHE_TTL_SECONDS = int(os.environ.get("FPL_CACHE_TTL_SECONDS", "3600"))

# suggest-best-squad: how long ingested global FPL data (player pool, teams,
# per-player history, fixtures) is considered fresh before a refresh re-fetches
# it. Deliberately longer than FPL_CACHE_TTL_SECONDS (per-entry data) - the
# global player pool changes far less often than one team's picks/history.
FPL_GLOBAL_DATA_FRESHNESS_SECONDS = int(
    os.environ.get("FPL_GLOBAL_DATA_FRESHNESS_SECONDS", str(24 * 60 * 60))
)

# Default number of upcoming gameweeks a strategy's fixture-difficulty term
# averages over when a strategy doesn't specify its own horizon.
DEFAULT_FIXTURE_HORIZON = int(os.environ.get("DEFAULT_FIXTURE_HORIZON", "5"))
