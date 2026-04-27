from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Core ──────────────────────────────────────────────────
SECRET_KEY = config('SECRET_KEY', default='dev-secret-change-in-production-!!!')
DEBUG       = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*', cast=Csv())

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'core',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]



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

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

# ── Database ──────────────────────────────────────────────
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
# Set this in .env — only the owner knows it.
REGISTRATION_CODE       = config('REGISTRATION_CODE',       default='')
REGISTRATION_ENABLED    = config('REGISTRATION_ENABLED',    default=True,  cast=bool)

# ── Cache (for tokens) ────────────────────────────────────
CACHES = {
    'default': {
        'BACKEND':  'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'token_cache',
    }
}
# Run once: python manage.py createcachetable

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

# ── Media / Static ────────────────────────────────────────
MEDIA_URL   = '/media/'
MEDIA_ROOT  = BASE_DIR / 'media'
STATIC_URL  = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# ── Localisation ──────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'Asia/Jerusalem'
USE_I18N      = True
USE_TZ        = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'