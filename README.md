# tabvio-agent

A Python browser agent that carries out tasks on websites and hands control back to a person for questions, MFA codes, and payments.

## Install

```sh
uv add tabvio-agent
uv run playwright install chromium
```

## Quickstart

Set `OPENAI_API_KEY` before running. Task output and questions arrive through `events()`; `start()` and `answer()` return `None`.

```python
import asyncio
from getpass import getpass

from tabvio_agent import AgentSettings, BrowserAgentSession


async def main():
    async with BrowserAgentSession(AgentSettings(headless=False)) as session:
        await session.start("Open https://example.org and summarize the page.")
        async for event in session.events():
            if event.event_type == "input.required":
                answer = await asyncio.to_thread(input, event.question + " ")
                await session.answer(answer)
            elif event.event_type == "sensitive_input.required":
                value = await asyncio.to_thread(getpass, event.prompt + " ")
                await session.submit_sensitive_input(event.request_id, value)
            elif event.event_type == "run.completed":
                print(event.output)
                break
            elif event.event_type == "run.failed":
                raise RuntimeError(event.error)


asyncio.run(main())
```

## Configuration

Pass configuration through `AgentSettings`:

- `strong_model` and `fast_model`: `provider:model` strings, defaulting to `openai:gpt-5.6-luna` and `openai:gpt-5-nano`.
- `headless`: defaults to `True`; set `False` to show the browser.
- `browser_channel`: Playwright browser channel; `None` uses bundled Chromium.
- `trace_path`: optional `pathlib.Path` for a Playwright trace.

The model provider reads its API key from its usual environment variable: `OPENAI_API_KEY` for the defaults.

## Credentials

Implement the async `CredentialProvider` protocol: `list_selected()` returns a tuple of `CredentialMetadata`, and `resolve(credential_id, domain)` returns a `CredentialSecret` after checking access to the domain. Pass it as `credential_provider` with the selected UUIDs in `selected_credential_ids` when constructing `BrowserAgentSession`. Secrets are filled into the page directly and never reach the model.

## Development

```sh
uv sync
uv run pytest
```

## License

Apache-2.0.
