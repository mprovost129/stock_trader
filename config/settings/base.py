"""Base Django settings shared across environments."""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / "subdir".
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


_load_dotenv_file(BASE_DIR / ".env")

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-change-me-in-production",
)

# Environment-specific modules should override this.
DEBUG = False


def _split_csv_env(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ALLOWED_HOSTS = _split_csv_env(os.getenv("DJANGO_ALLOWED_HOSTS", ""))

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Local apps
    "apps.marketdata",
    "apps.strategies",
    "apps.signals",
    "apps.portfolios",
    "apps.journal",
    "apps.risk",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "apps" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.portfolios.context_processors.active_watchlist",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "login"

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Default primary key field type
# https://docs.djangoproject.com/en/6.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Market data provider configuration (read-only) ---
# Milestone 1:
# - Stocks daily default: Polygon when a key exists, otherwise Yahoo Finance fallback
# - Stocks intraday optional: Polygon (requires POLYGON_API_KEY)
# - Crypto: Coinbase candles (public endpoint; no auth)
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
STOCK_DAILY_PROVIDER = os.getenv("STOCK_DAILY_PROVIDER", "polygon" if POLYGON_API_KEY else "yahoo")


# --- Alerts configuration (Milestone 2) ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
ALERT_MIN_PRICE = os.getenv("ALERT_MIN_PRICE", "")   # e.g. "5" — suppress alerts below this price
ALERT_MAX_PRICE = os.getenv("ALERT_MAX_PRICE", "")   # e.g. "500" — suppress alerts above this price
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "30"))
ALERT_MAX_PER_DAY = int(os.getenv("ALERT_MAX_PER_DAY", "12"))
ALERT_MAX_SIGNAL_AGE_MINUTES = int(os.getenv("ALERT_MAX_SIGNAL_AGE_MINUTES", "4320"))
ALERT_MIN_SCORE_EVENT = float(os.getenv("ALERT_MIN_SCORE_EVENT", "0.80"))
ALERT_MIN_SCORE_STATE = float(os.getenv("ALERT_MIN_SCORE_STATE", "0.60"))
ALERT_STATE_CHANGE_ONLY = _env_bool("ALERT_STATE_CHANGE_ONLY", True)
EQUITY_ALERT_SESSION_START = os.getenv("EQUITY_ALERT_SESSION_START", "09:30")
EQUITY_ALERT_SESSION_END = os.getenv("EQUITY_ALERT_SESSION_END", "16:00")

POSITION_DETERIORATION_ALERT_PCT = float(os.getenv("POSITION_DETERIORATION_ALERT_PCT", "2.0"))
POSITION_STOP_ALERT_DISTANCE_PCT = float(os.getenv("POSITION_STOP_ALERT_DISTANCE_PCT", "1.0"))
POSITION_ALERT_COOLDOWN_MINUTES = int(os.getenv("POSITION_ALERT_COOLDOWN_MINUTES", "120"))
PAPER_TRADE_DEFAULT_TRAILING_STOP_PCT = os.getenv("PAPER_TRADE_DEFAULT_TRAILING_STOP_PCT", "")

# --- Scheduler configuration (Milestone 3) ---
SCHEDULER_INTERVAL_SECONDS = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "300"))
SCHEDULER_STOCK_TIMEFRAME = os.getenv("SCHEDULER_STOCK_TIMEFRAME", "1d")
SCHEDULER_CRYPTO_TIMEFRAME = os.getenv("SCHEDULER_CRYPTO_TIMEFRAME", "1d")
SCHEDULER_STOCK_PROVIDER = os.getenv("SCHEDULER_STOCK_PROVIDER", "")
SCHEDULER_CRYPTO_PROVIDER = os.getenv("SCHEDULER_CRYPTO_PROVIDER", "")

SCHEDULER_MAX_SYMBOLS_PER_CYCLE = int(os.getenv("SCHEDULER_MAX_SYMBOLS_PER_CYCLE", "25"))
SCHEDULER_THROTTLE_SECONDS = float(os.getenv("SCHEDULER_THROTTLE_SECONDS", "0"))
SCHEDULER_MARKET_AWARE = _env_bool("SCHEDULER_MARKET_AWARE", True)
SCHEDULER_OPEN_SLEEP_SECONDS = int(os.getenv("SCHEDULER_OPEN_SLEEP_SECONDS", "300"))
SCHEDULER_CLOSED_SLEEP_SECONDS = int(os.getenv("SCHEDULER_CLOSED_SLEEP_SECONDS", "3600"))
SCHEDULER_HEALTHCHECK_EVERY = int(os.getenv("SCHEDULER_HEALTHCHECK_EVERY", "12"))


ALERT_DELIVERY_DISCORD_ENABLED = os.getenv("ALERT_DELIVERY_DISCORD_ENABLED", "true").lower() == "true"
ALERT_DELIVERY_EMAIL_ENABLED = os.getenv("ALERT_DELIVERY_EMAIL_ENABLED", "false").lower() == "true"
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
ALERT_DELIVERY_HEALTH_WINDOW_HOURS = int(os.getenv("ALERT_DELIVERY_HEALTH_WINDOW_HOURS", "24"))
ALERT_DROUGHT_MINUTES = int(os.getenv("ALERT_DROUGHT_MINUTES", "240"))
ALERT_FAILURE_STREAK_THRESHOLD = int(os.getenv("ALERT_FAILURE_STREAK_THRESHOLD", "3"))
ALERT_ESCALATION_COOLDOWN_MINUTES = int(os.getenv("ALERT_ESCALATION_COOLDOWN_MINUTES", "180"))
SCHEDULER_DELIVERY_ESCALATION_EVERY = int(os.getenv("SCHEDULER_DELIVERY_ESCALATION_EVERY", "1"))
SCHEDULER_POSITION_SYNC_EVERY = int(os.getenv("SCHEDULER_POSITION_SYNC_EVERY", "1"))

ALERT_RECOVERY_COOLDOWN_MINUTES = int(os.getenv("ALERT_RECOVERY_COOLDOWN_MINUTES", "60"))
SCHEDULER_DELIVERY_RECOVERY_EVERY = int(os.getenv("SCHEDULER_DELIVERY_RECOVERY_EVERY", "1"))

HELD_POSITION_ALERT_COOLDOWN_MINUTES = int(os.getenv("HELD_POSITION_ALERT_COOLDOWN_MINUTES", "240"))
HELD_POSITION_DETERIORATION_ALERT_PCT = float(os.getenv("HELD_POSITION_DETERIORATION_ALERT_PCT", "5.0"))
HELD_POSITION_REVIEW_WARNING_PCT = float(os.getenv("HELD_POSITION_REVIEW_WARNING_PCT", "2.5"))
HELD_POSITION_SELL_ON_SHORT_WITH_LOSS = os.getenv("HELD_POSITION_SELL_ON_SHORT_WITH_LOSS", "true").lower() == "true"
SCHEDULER_HELD_POSITION_CHECK_EVERY = int(os.getenv("SCHEDULER_HELD_POSITION_CHECK_EVERY", "1"))

SCHEDULER_PORTFOLIO_SNAPSHOT_EVERY = int(os.getenv("SCHEDULER_PORTFOLIO_SNAPSHOT_EVERY", "4"))
PORTFOLIO_HEALTH_DETERIORATION_THRESHOLD = int(os.getenv("PORTFOLIO_HEALTH_DETERIORATION_THRESHOLD", "10"))
PORTFOLIO_HEALTH_ALERT_COOLDOWN_MINUTES = int(os.getenv("PORTFOLIO_HEALTH_ALERT_COOLDOWN_MINUTES", "120"))
