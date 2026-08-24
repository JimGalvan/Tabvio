# Tabvio recruiter demo MVP

## Outcome

Deliver a phone-friendly browser-agent demo that starts a deterministic task, streams useful activity over HTTP, shows the browser as a watch-only live view, accepts user input when the agent pauses, and retains an audit trail in SQLite.

## Scope

### Included

- One active agent run at a time
- Fresh anonymous Playwright context for each run
- UUID identifiers for runs and events
- UTC timestamps for record creation and updates
- LangGraph message, tool, and custom-event streaming over server-sent events
- Watch-only browser frames over an MJPEG HTTP response
- Human input through a LangGraph interrupt and resume command
- Run status, meaningful events, answers, errors, and final output in SQLite
- Responsive single-page viewer served by the API
- Cancellation and bounded execution time

### Deferred

- Concurrent browser sessions
- Authentication and multi-tenancy
- Distributed workers, queues, and restart recovery
- PostgreSQL and object storage
- Browser takeover or remote input
- Durable video and screenshot archives
- MCP exposure

## Runtime shape

1. `POST /api/runs` creates a run and starts the agent in a background task.
2. `GET /api/runs/{run_id}/stream` replays stored events and continues with live SSE events.
3. The deterministic tools publish normalized browser activity while LangGraph streams assistant output.
4. `GET /api/runs/{run_id}/screen.mjpeg` sends the newest browser frame and drops stale frames.
5. A user-input tool pauses the graph. `POST /api/runs/{run_id}/input` resumes it with the same thread ID.
6. SQLite stores the run record and normalized event ledger. LangGraph checkpoints remain in memory for the demo.

## Run states

- `queued`
- `running`
- `waiting_for_input`
- `succeeded`
- `failed`
- `cancelled`
- `timed_out`

## Acceptance criteria

- A user can submit a task from the viewer and receive a UUID run ID.
- Status and browser actions appear before the run completes.
- The browser image updates without refreshing the page.
- An agent question enables the response form and the submitted answer resumes the same run.
- A completed or failed run has a persisted final state and ordered event history.
- A second run is rejected while another run is active.
- Cancelling a run closes its browser and records the cancellation.
- The viewer is usable on a phone-sized viewport.

## Delivery order

1. Agent runtime extraction and normalized event hooks
2. Run lifecycle, SQLite repository, and HTTP endpoints
3. SSE and MJPEG streams
4. Human input interrupt and resume
5. Responsive viewer
6. Automated checks and a complete local smoke test

