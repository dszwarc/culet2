from .base import *

DEBUG = False

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1"],
)

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["http://localhost:8000", "http://127.0.0.1:8000"],
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)

X_FRAME_OPTIONS = "DENY"

SECURE_CONTENT_TYPE_NOSNIFF = True
REFERRER_POLICY = "same-origin"