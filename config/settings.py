from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Core ──────────────────────────────────────────────────
SECRET_KEY    = config('SECRET_KEY', default='dev-secret-change-in-production-!!!')
DEBUG         = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*', cast=Csv())

SITE_URL = config('SITE_URL', default='http://10.0.0.13:8000')

# ── Proxy / SSL ───────────────────────────────────────────
# Railway terminates TLS at its edge and forwards the request to gunicorn
# as plain HTTP, setting X-Forwarded-Proto: https. Without this line,
# Django thinks the request arrived as HTTP, which can cause spurious
# 301 redirects that downgrade POST → GET (giving 405 Method Not Allowed
# on every form submission). Trusting this header is safe BECAUSE
# Railway's proxy strips client-supplied X-Forwarded-* headers before
# forwarding, so the only source of this header is Railway itself.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Also tell Django that requests through the proxy CAN be considered
# "secure" for purposes of CSRF same-origin checks etc.
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',

    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
    'rest_framework',
    'cloudinary_storage',
    'cloudinary',
    'corsheaders',
    'core',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ── Database — MySQL ──────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE':   'django.db.backends.mysql',
        'NAME':     config('DB_NAME',     default='truckforce_db'),
        'USER':     config('DB_USER',     default='root'),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST':     config('DB_HOST',     default='127.0.0.1'),
        'PORT':     config('DB_PORT',     default='3306'),
        'OPTIONS': {
            'charset':      'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

# ── Registration (env-only, never stored in DB) ───────────
REGISTRATION_CODE    = config('REGISTRATION_CODE',    default='TF-2025')
REGISTRATION_ENABLED = config('REGISTRATION_ENABLED', default=True, cast=bool)

# ── Cache (for tokens) ────────────────────────────────────
CACHES = {
    'default': {
        'BACKEND':  'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'token_cache',
    }
}
# Run once after migrate: python manage.py createcachetable

# ── REST Framework ────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
    ],
}

# ── CORS ──────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL', default=True, cast=bool)

# ── Third-party services ──────────────────────────────────
# Mapbox: used by route_optimizer.py for stop-order optimization
# and by the public tracking page's live map. Read at module import,
# so a restart is required after changing the env var.
MAPBOX_TOKEN = config('MAPBOX_TOKEN', default='')

# SITE_URL: absolute base URL of this Django deployment. Used by
# tracking_page (HTML) to build links the client can share. Falls back to
# whatever request.build_absolute_uri returns if unset.
SITE_URL = config('SITE_URL', default='')

# ── Media / Static ────────────────────────────────────────
import cloudinary
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': config('CLOUDINARY_CLOUD_NAME', default=''),
    'API_KEY':    config('CLOUDINARY_API_KEY',    default=''),
    'API_SECRET': config('CLOUDINARY_API_SECRET', default=''),
}
if config('CLOUDINARY_CLOUD_NAME', default=''):
    DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
    MEDIA_URL = f"https://res.cloudinary.com/{config('CLOUDINARY_CLOUD_NAME', default='')}/image/upload/"
else:
    DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
    MEDIA_URL  = '/media/'
    MEDIA_ROOT = BASE_DIR / 'media'


STATIC_URL  = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# ── Auth ──────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── Localisation ──────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'Asia/Jerusalem'
USE_I18N      = True
USE_TZ        = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


import os, json, tempfile
FIREBASE_CREDENTIALS_JSON = config('FIREBASE_CREDENTIALS_JSON', default='')
if FIREBASE_CREDENTIALS_JSON:
    _creds = json.loads(FIREBASE_CREDENTIALS_JSON)
    _tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump(_creds, _tmp)
    _tmp.close()
    FIREBASE_CREDENTIALS_PATH = _tmp.name
else:
    FIREBASE_CREDENTIALS_PATH = config('FIREBASE_CREDENTIALS_PATH',
                                       default=str(BASE_DIR / 'firebase-credentials.json'))