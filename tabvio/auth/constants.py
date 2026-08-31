"""Fixed values for sign-in and session handling."""

SESSION_COOKIE_NAME = "wos_session"
SESSION_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30

LOGIN_STATE_COOKIE_NAME = "wos_login_state"
LOGIN_STATE_COOKIE_MAX_AGE_SECONDS = 60 * 10

SIGNED_IN_PATH = "/app"

# How long a rotated session stays reachable under the cookie value it
# replaced. Long enough to cover requests already in flight when the rotation
# happened, short enough that a revoked session is not honoured for long.
REFRESHED_SESSION_MEMO_SECONDS = 120.0

USER_CACHE_SECONDS = 300.0
SESSION_CACHE_ENTRIES = 512
