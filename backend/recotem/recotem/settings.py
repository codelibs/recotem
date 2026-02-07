from datetime import timedelta
from pathlib import Path
from typing import List
from urllib.parse import urlparse, urlunparse

import environ

env = environ.Env(DEBUG=(bool, True))


def _inject_redis_password_if_missing(url: str, password: str) -> str:
    """Inject REDIS_PASSWORD into redis:// URLs that do not already have auth."""
    if not password:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"redis", "rediss"}:
        return url
    if parsed.password:
        return url

    host = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    if parsed.username:
        auth = f"{parsed.username}:{password}@"
    else:
        auth = f":{password}@"
    netloc = f"{auth}{host}{port}"
    return urlunparse(parsed._replace(netloc=netloc))

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY", default="VeryBadSecret@ChangeThis")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost"])

# Runtime safety checks for production
if not DEBUG:
    if SECRET_KEY == "VeryBadSecret@ChangeThis":
        raise RuntimeError(
            "SECRET_KEY must be changed from default for production (DEBUG=False). "
            "Set the SECRET_KEY environment variable to a unique, unpredictable value."
        )
    if len(SECRET_KEY) < 50:
        raise RuntimeError(
            "SECRET_KEY must be at least 50 characters long for production (DEBUG=False). "
            "Generate a strong random key with: python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'"
        )
    if "*" in ALLOWED_HOSTS:
        raise RuntimeError(
            "ALLOWED_HOSTS must not contain '*' in production (DEBUG=False). "
            "Set the ALLOWED_HOSTS environment variable to your domain names."
        )

# Security headers (production)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_BROWSER_XSS_FILTER = True
if not DEBUG:
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", default=31536000))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)


# Application definition

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "channels",
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "corsheaders",
    "dj_rest_auth",
    "django_filters",
    "django_extensions",
    "django.contrib.staticfiles",
    "recotem.api",
    "django_celery_results",
    "drf_spectacular",
    "django_cleanup.apps.CleanupConfig",  # always the last one
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# CORS configuration
# For same-origin deployments via nginx proxy, CORS is not needed.
# For cross-subdomain deployments, set CORS_ALLOWED_ORIGINS env var.
_cors_origins = env.list("CORS_ALLOWED_ORIGINS", default=[])
if _cors_origins:
    CORS_ALLOWED_ORIGINS = _cors_origins
else:
    # Same-origin: allow all only in debug mode
    CORS_ALLOW_ALL_ORIGINS = DEBUG

CORS_ALLOW_CREDENTIALS = True

# CSRF trusted origins (required for cross-domain frontend/backend deployments)
_csrf_origins = env.list("CSRF_TRUSTED_ORIGINS", default=[])
if _csrf_origins:
    CSRF_TRUSTED_ORIGINS = _csrf_origins


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "dj_rest_auth.jwt_auth.JWTCookieAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON_RATE", default="20/min"),
        "user": env("THROTTLE_USER_RATE", default="100/min"),
        "login": env("THROTTLE_LOGIN_RATE", default="5/min"),
    },
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "UPLOADED_FILES_USE_URL": False,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# dj-rest-auth settings (5.x+ unified configuration)
REST_AUTH = {
    "USER_DETAILS_SERIALIZER": "recotem.api.serializers.UserDetailsSerializer",
    "SESSION_LOGIN": True,
    "USE_JWT": True,
    "JWT_AUTH_REFRESH_COOKIE": "refresh-token",
    "JWT_AUTH_COOKIE_USE_CSRF": True,
}

ROOT_URLCONF = "recotem.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": ["dist"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "recotem.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases

_DEFAULT_DB_URL = f'sqlite:///{(BASE_DIR / "data" / "db.sqlite3")}'
DATABASE_URL = env("DATABASE_URL", default=_DEFAULT_DB_URL)
DATABASES = {"default": env.db("DATABASE_URL", default=_DEFAULT_DB_URL)}
DATABASES["default"]["CONN_MAX_AGE"] = int(env("CONN_MAX_AGE", default=0))


# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

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
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Data & Trained Model storage

_STORAGE_TYPE = env("RECOTEM_STORAGE_TYPE", default="")
if not _STORAGE_TYPE:
    MEDIA_ROOT = BASE_DIR / "data"
elif _STORAGE_TYPE == "S3":
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        raise RuntimeError(
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set when RECOTEM_STORAGE_TYPE=S3"
        )
    AWS_LOCATION = env("AWS_LOCATION", default="")
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)

    # Django 5.1+ STORAGES configuration
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "dist" / "static"

STATICFILES_DIRS: List[Path] = []


# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Celery settings
REDIS_PASSWORD = env("REDIS_PASSWORD", default="")
CELERY_BROKER_URL = _inject_redis_password_if_missing(
    env("CELERY_BROKER_URL", default="redis://localhost:6379/0"),
    REDIS_PASSWORD,
)
# CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER", cast=bool, default=False)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"
CELERY_TASK_SERIALIZER = "json"


SIMPLE_JWT = {
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "ACCESS_TOKEN_LIFETIME": timedelta(
        seconds=int(env("ACCESS_TOKEN_LIFETIME", default=300))
    ),
}


# ASGI application
ASGI_APPLICATION = "recotem.asgi.application"

# Channel layers (Redis db=1, separate from Celery broker on db=0)
_CHANNELS_REDIS_URL = CELERY_BROKER_URL.rsplit("/", 1)[0] + "/1"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [_CHANNELS_REDIS_URL],
        },
    },
}


# Cache configuration (Redis db 2)
_CACHE_REDIS_URL = _inject_redis_password_if_missing(
    env("CACHE_REDIS_URL", default="redis://localhost:6379/2"),
    REDIS_PASSWORD,
)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _CACHE_REDIS_URL,
        "TIMEOUT": 300,
        "KEY_PREFIX": env("CACHE_KEY_PREFIX", default="recotem"),
    },
}

# Upload size limit (bytes, default 500MB to match nginx client_max_body_size)
DATA_UPLOAD_MAX_MEMORY_SIZE = int(
    env("DATA_UPLOAD_MAX_MEMORY_SIZE", default=524288000)
)

# Model cache size (number of trained models kept in LRU cache)
MODEL_CACHE_SIZE = int(env("MODEL_CACHE_SIZE", default=8))

# Model cache timeout (seconds)
MODEL_CACHE_TIMEOUT = int(env("MODEL_CACHE_TIMEOUT", default=3600))

# Celery task time limits (seconds)
CELERY_TASK_TIME_LIMIT = int(env("CELERY_TASK_TIME_LIMIT", default=3600))
CELERY_TASK_SOFT_TIME_LIMIT = int(env("CELERY_TASK_SOFT_TIME_LIMIT", default=3480))


# Logging — structured JSON in production, simple in development

import logging as _logging
import re as _re


class _SensitiveDataFilter(_logging.Filter):
    """Mask passwords in DATABASE_URL, AWS credentials, and other sensitive patterns."""

    _URL_PASSWORD_RE = _re.compile(r"://([^:]+):([^@]+)@")
    _AWS_KEY_RE = _re.compile(
        r"(AWS_SECRET_ACCESS_KEY|aws_secret_access_key)\s*[=:]\s*\S+",
    )
    _AWS_SESSION_RE = _re.compile(
        r"(AWS_SESSION_TOKEN|aws_session_token)\s*[=:]\s*\S+",
    )

    def filter(self, record: _logging.LogRecord) -> bool:
        if record.args and isinstance(record.args, tuple):
            record.args = tuple(self._mask(a) for a in record.args)
        record.msg = self._mask(record.msg)
        return True

    def _mask(self, value):
        if isinstance(value, str):
            value = self._URL_PASSWORD_RE.sub(r"://\1:***@", value)
            value = self._AWS_KEY_RE.sub(r"\1=***", value)
            value = self._AWS_SESSION_RE.sub(r"\1=***", value)
        return value


_LOG_FORMATTERS = {
    "simple": {
        "format": "%(levelname)s %(asctime)s %(name)s %(message)s",
    },
    "json": {
        "()": "pythonjsonlogger.json.JsonFormatter",
        "format": "%(asctime)s %(name)s %(levelname)s %(message)s",
    },
}
_LOG_FORMATTER = "simple" if DEBUG else "json"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": _LOG_FORMATTERS,
    "filters": {
        "sensitive_data": {
            "()": "recotem.settings._SensitiveDataFilter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": _LOG_FORMATTER,
            "filters": ["sensitive_data"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": env("LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": env("DJANGO_LOG_LEVEL", default="WARNING"),
            "propagate": False,
        },
        "celery": {
            "handlers": ["console"],
            "level": env("CELERY_LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
        "recotem": {
            "handlers": ["console"],
            "level": env("LOG_LEVEL", default="INFO"),
            "propagate": False,
        },
    },
}


# drf-spectacular settings
SPECTACULAR_SETTINGS = {
    "TITLE": "Recotem API",
    "DESCRIPTION": "API for building and managing recommendation systems",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v[0-9]+/",
    "COMPONENT_SPLIT_REQUEST": True,
    "TAGS": [
        {"name": "Projects", "description": "Project management"},
        {"name": "Data", "description": "Training data upload and management"},
        {"name": "Tuning", "description": "Parameter tuning job management"},
        {"name": "Models", "description": "Trained model management and recommendations"},
        {"name": "Configuration", "description": "Split, evaluation, and model configurations"},
        {"name": "Auth", "description": "Authentication and user management"},
    ],
}
