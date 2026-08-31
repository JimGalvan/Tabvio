# Tabvio

Tabvio web browser agent that can perform tasks with autonomy.

## Development

Install dependencies and the Playwright browser:

```powershell
uv sync
uv run playwright install chromium
```

Start the application:

```powershell
uv run uvicorn tabvio.main:app --reload
```

Run the checks:

```powershell
uv run pytest
uv run ruff check .
```

## Accounts

Sign-in runs through [WorkOS AuthKit](https://workos.com/docs/authkit). Create a
WorkOS application and set `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, and
`WORKOS_COOKIE_PASSWORD` as described in `.env.example`.

The callback URL is derived from the request, so it needs no configuration, but
it does have to be registered under Redirects in the WorkOS dashboard. WorkOS
environments are separate, each with its own keys and redirect settings, so
register the local URLs in Staging and the deployed URLs in Production:

| Dashboard field | Staging | Production |
| --- | --- | --- |
| Redirect URI | `http://localhost:8000/callback` | `https://<host>/callback` |
| Sign-out URI | `http://localhost:8000/login` | `https://<host>/login` |
| Initiate login URL | `http://localhost:8000/login` | `https://<host>/login` |

Requests to anything other than localhost are treated as HTTPS, because Railway
terminates TLS ahead of the app and the request itself still reads as plain
HTTP. Session cookies are marked `Secure` under the same rule.

`WORKOS_COOKIE_PASSWORD` must be a Fernet key, not any thirty-two character
string. Generate one with
`uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

Every run belongs to the account that started it, and runs recorded before
accounts existed belong to nobody, so they are no longer reachable.

Runtime configuration is documented in `.env.example`. SQLite data is stored in the ignored `data/` directory.
