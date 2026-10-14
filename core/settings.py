"""
Django settings for EasyTravel project.
Configured for production-readiness, security, and flexible deployment.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load local environment variables from .env
load_dotenv(BASE_DIR / '.env')

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

DEBUG = os.getenv('DJANGO_DEBUG', 'False').lower() in ('true', '1', 't', 'yes')

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-key"
    else:
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured("DJANGO_SECRET_KEY est obligatoire en production.")

hosts_env = os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost,testserver,easytravel.cm,www.easytravel.cm,169.58.52.142')
ALLOWED_HOSTS = [h.strip() for h in hosts_env.split(',') if h.strip()]
for h in ['easytravel.cm', 'www.easytravel.cm', '169.58.52.142', 'easytravel.mslogitech.com']:
    if h not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(h)
if DEBUG and '*' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.extend(['127.0.0.1', 'localhost', 'testserver'])

# Configuration Reverse Proxy Nginx & SSL HTTPS
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# Domaines CSRF autorisés pour soumission de formulaires en HTTPS
csrf_env = os.getenv('CSRF_TRUSTED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = [o.strip() for o in csrf_env.split(',') if o.strip()]
default_csrf = [
    'https://easytravel.cm',
    'https://www.easytravel.cm',
    'http://easytravel.cm',
    'http://www.easytravel.cm',
    'https://easytravel.mslogitech.com',
    'http://easytravel.mslogitech.com',
    'https://*.mslogitech.com',
    'http://169.58.52.142',
    'http://127.0.0.1:8000',
    'http://localhost:8000',
]
for origin in default_csrf:
    if origin not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(origin)

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SAMESITE = 'Lax'

# Application definition
INSTALLED_APPS = [
    # Django Core
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    
    # Local Apps
    'accounts',
    'agencies',
    'travel',
    'bookings',
    'payments',
]

AUTH_USER_MODEL = 'accounts.User'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'agencies.context_processors.agency_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# Database Configuration
# Defaults to SQLite with WAL mode locally; switches to PostgreSQL if DATABASE_URL is set
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 30,
            'init_command': 'PRAGMA journal_mode=WAL; PRAGMA busy_timeout=30000; PRAGMA synchronous=NORMAL;',
        },
    }
}

database_url = os.getenv('DATABASE_URL')
if database_url:
    DATABASES['default'] = dj_database_url.config(default=database_url, conn_max_age=600)

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'fr-FR'
TIME_ZONE = 'Africa/Douala'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# WhiteNoise storage optimization for production
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage" if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# Media Files (User uploads, Agency Logos, Vehicle Photos)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Email Service
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend' if DEBUG else 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 't')
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False').lower() in ('true', '1', 't')
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_TIMEOUT = 5
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'EasyTravel <noreply@easytravel.mslogitech.com>')
SUPPORT_EMAIL = os.getenv('SUPPORT_EMAIL', 'support@easytravel.mslogitech.com')
SITE_URL = os.getenv('SITE_URL', 'https://easytravel.cm' if not DEBUG else 'http://127.0.0.1:8000')

# Authentication Redirects
LOGIN_REDIRECT_URL = '/compte/mes-reservations/'
LOGOUT_REDIRECT_URL = '/'
LOGIN_URL = '/compte/connexion/'

# Déconnexion automatique après inactivité (30 minutes)
SESSION_COOKIE_AGE = 1800  # 30 minutes x 60 secondes
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Production Security Headers (when DEBUG=False)
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1', 't')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_HSTS_SECONDS = 31536000  # 1 an
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# External Services & APIs
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

# Payment Gateways (CinetPay & Campay)
CINETPAY_SITE_ID = os.getenv('CINETPAY_SITE_ID', '')
CINETPAY_API_KEY = os.getenv('CINETPAY_API_KEY', '')
CINETPAY_CURRENCY = os.getenv('CINETPAY_CURRENCY', 'XAF')
CINETPAY_RETURN_URL = os.getenv('CINETPAY_RETURN_URL', '')
CINETPAY_NOTIFY_URL = os.getenv('CINETPAY_NOTIFY_URL', '')

CAMPAY_APP_USERNAME = os.getenv('CAMPAY_APP_USERNAME', '')
CAMPAY_APP_PASSWORD = os.getenv('CAMPAY_APP_PASSWORD', '')
CAMPAY_ENVIRONMENT = os.getenv('CAMPAY_ENVIRONMENT', 'sandbox')

# SasPay Configuration
SASPAY_API_KEY = os.getenv('SASPAY_API_KEY', '')
SASPAY_WEBHOOK_SECRET = os.getenv('SASPAY_WEBHOOK_SECRET', '')
SASPAY_PROJECT_ID = os.getenv('SASPAY_PROJECT_ID', 'easytravel')

# Payment Simulation & Sandbox Mode (True by default for tests & demo)
PAYMENT_SIMULATION_MODE = DEBUG and os.getenv('PAYMENT_SIMULATION_MODE', 'False').lower() in ('true', '1', 't', 'yes')


# --- FEES & COMMISSIONS ---
PLATFORM_FEE_PERCENTAGE = 0.03


