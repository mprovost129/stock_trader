import os
from pathlib import Path

import dj_database_url

from .base import *


def _env_bool(name: str, default: bool) -> bool:
	raw = os.getenv(name)
	if raw is None:
		return default
	return raw.strip().lower() in {"1", "true", "yes", "on"}


DEBUG = False

if not SECRET_KEY or SECRET_KEY == "django-insecure-change-me-in-production":
	raise RuntimeError("DJANGO_SECRET_KEY must be set in production")

if not ALLOWED_HOSTS:
	raise RuntimeError("DJANGO_ALLOWED_HOSTS must be set in production")

render_external_hostname = (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").strip()
if render_external_hostname and render_external_hostname not in ALLOWED_HOSTS:
	ALLOWED_HOSTS.append(render_external_hostname)

csrf_from_env = [
	host.strip()
	for host in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
	if host.strip()
]
if render_external_hostname:
	csrf_from_env.append(f"https://{render_external_hostname}")
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys(csrf_from_env))

database_url = (os.getenv("DATABASE_URL") or "").strip()
if database_url:
	db_config = dj_database_url.parse(
		database_url,
		conn_max_age=int(os.getenv("DJANGO_DB_CONN_MAX_AGE", "600")),
		conn_health_checks=True,
		ssl_require=_env_bool("DJANGO_DB_SSL_REQUIRE", True),
	)
	db_schema = (os.getenv("DJANGO_DB_SCHEMA") or "").strip()
	if db_schema:
		db_config.setdefault("OPTIONS", {})["options"] = f"-c search_path={db_schema},public"
	DATABASES["default"] = db_config
else:
	use_render_disk_sqlite = _env_bool("DJANGO_USE_RENDER_DISK_SQLITE", True)
	render_disk_mount = (os.getenv("RENDER_DISK_MOUNT_PATH") or "").strip()
	if use_render_disk_sqlite and render_disk_mount:
		sqlite_path = Path(render_disk_mount) / "db.sqlite3"
		sqlite_path.parent.mkdir(parents=True, exist_ok=True)
		DATABASES["default"] = {
			"ENGINE": "django.db.backends.sqlite3",
			"NAME": sqlite_path,
		}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
	"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
	"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
WHITENOISE_MAX_AGE = int(os.getenv("WHITENOISE_MAX_AGE", "31536000"))

if "whitenoise.middleware.WhiteNoiseMiddleware" not in MIDDLEWARE:
	MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = _env_bool("DJANGO_SESSION_COOKIE_SECURE", True)
CSRF_COOKIE_SECURE = _env_bool("DJANGO_CSRF_COOKIE_SECURE", True)
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = _env_bool("DJANGO_SECURE_HSTS_PRELOAD", True)
