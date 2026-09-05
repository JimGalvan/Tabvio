# E2E test pages

Two hand-driven test sites for exercising Tabvio against a real browser:

- `index.html` — one page of nested iframes, covered below.
- `nav/` — five linked pages for navigation and tabs, no iframes. See `nav/README.md`.

## The iframe page

`index.html` is a hand-driven page for exercising Tabvio against a real browser.

Serve it (relative links and `window.open` behave better over http than `file://`):

    python -m http.server 8001 --directory tests/e2e

Then point a run at `http://localhost:8001/index.html`.

## What is on the page

Four frames, each with a `name` so they are easy to tell apart in a snapshot:

- **main frame** — a button, a link that opens a new tab, a scroll area, and enough
  filler that the page itself scrolls.
- **frame-a** — a button that changes text, and its own scroll area.
- **frame-b** — a box, a text input, a select, a checkbox, and a submit button that
  writes the values back onto the page.
- **frame-c** — a link and a button that each open a new tab, a counter button, and a
  nested iframe (**frame-c-nested**) two levels deep.

Things worth checking: switching between frames, acting on an element in one frame and
confirming the others are untouched, scrolling inside a frame rather than the page,
following a new tab and switching back, and reaching the nested frame.

## Tasks

`tasks/` holds one task per `.txt` file, written the way a user would type it. Paste one
into a run as-is. `tasks/EXPECTED.md` says what each should produce and which part of
the browser layer it leans on.
