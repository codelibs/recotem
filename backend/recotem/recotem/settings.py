from datetime import timedelta
from pathlib import Path
from typing import List

import environ

env = environ.Env(DEBUG=(bool, True))

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY", default="VeryBadSecret@ChangeThis")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")

ALLOWED_HOSTS = ["*"]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "rest_framework",
    "django_filters",
    "django_extensions",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "recotem.api",
    "django_celery_results",
    "drf_spectacular",
    "django_cleanup.apps.CleanupConfig",  # always the last one
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
]
MIDDLEWARE_CLASSES = ("whitenoise.middleware.WhiteNoiseMiddleware",)

RECOTEM_API_AUTH = [
    "rest_framework_simplejwt.authentication.JWTAuthentication",
]


if env("RECOTEM_TESTING", cast=bool, default=False):
    RECOTEM_API_AUTH.append(
        "rest_framework.authentication.SessionAuthentication",
    )


REST_FRAMEWORK = {
    # "DEFAULT_AUTHENTICATION_CLASSES": [
    #    "rest_framework.authentication.BasicAuthentication",
    # ],
    "DEFAULT_AUTHENTICATION_CLASSES": RECOTEM_API_AUTH,
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "UPLOADED_FILES_USE_URL": False,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
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
# https://docs.djangoproject.com/en/3.2/ref/settings/#databases

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f'sqlite:///{(BASE_DIR / "data" / "db.sqlite3")}',
    )
}
DATABASE_URL = env(
    "DATABASE_URL", default=f'sqlite:///{(BASE_DIR / "data" / "db.sqlite3")}'
)


# Password validation
# https://docs.djangoproject.com/en/3.2/ref/settings/#auth-password-validators

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
# https://docs.djangoproject.com/en/3.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_L10N = True

USE_TZ = True

# Data & Trained Model storage

_DEFAULT_FILE_STORAGE = env("DEFAULT_FILE_STORAGE", default="")
if _DEFAULT_FILE_STORAGE:
    DEFAULT_FILE_STORAGE = _DEFAULT_FILE_STORAGE
else:
    MEDIA_ROOT = BASE_DIR / "data"

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.2/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "dist" / "static"

STATICFILES_DIRS: List[Path] = []


##########
# STATIC #
##########

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# Default primary key field type
# https://docs.djangoproject.com/en/3.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Celery settings
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="amqp://guest:guest@localhost")
# CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER", cast=bool, default=False)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"
CELERY_TASK_SERIALIZER = "json"

# JWT
SIMPLE_JWT = dict(
    ACCESS_TOKEN_LIFETIME=timedelta(
        seconds=env("JWT_ACCESS_TOKEN_LIFETIME_IN_SECONDS", cast=float, default=1000)
    )
)
