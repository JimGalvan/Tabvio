# Tabvio MVP demo runbook

## First-time setup

```powershell
uv sync
uv run playwright install chromium
```

Create a `.env` file with the model provider credentials already used by the deterministic agent notebook.

## Start the app

```powershell
$env:TABVIO_HEADLESS = "true"
uv run uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## Phone access

With the app running, start a temporary tunnel in a second terminal:

```powershell
ngrok http 8000
```

Open the HTTPS forwarding URL printed by ngrok. The MVP has no authentication, so treat the URL as temporary, do not share it publicly, and stop ngrok after the demo.

If ngrok reports `ERR_NGROK_334`, the account's assigned endpoint is already online. Stop that existing endpoint from its current session or the ngrok dashboard, then run the command again. Do not enable pooling because it would mix traffic between applications.

## Demo prompts

Straight-through run:

> Open example.com and tell me the main heading.

Human-in-the-loop run:

> Open example.com, ask me for one word, then report the page heading and my word.

The second prompt demonstrates the live browser view, action ledger, user-input pause, resume on the same agent thread, and persisted result.

## Expected behavior

- The latest-action line and full activity ledger update while the agent works.
- The browser panel refreshes through cache-free JPEG snapshots.
- A temporary screenshot failure reports a paused live view and retries automatically.
- A question card appears when the agent needs an answer.
- Submitting the answer resumes the existing run.
- The completed answer remains visible and the run is stored in SQLite.
- Starting another run while one is active returns HTTP 409.

## Stop

Press `Ctrl+C` in the app and ngrok terminals.
