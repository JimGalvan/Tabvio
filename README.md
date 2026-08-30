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

Runtime configuration is documented in `.env.example`. SQLite data is stored in the ignored `data/` directory.
