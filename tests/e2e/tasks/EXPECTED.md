# Expected outcomes

The `.txt` files hold nothing but the task text so they can be fed to the agent
verbatim. The answers live here instead, so a task file never leaks its own answer
into the prompt.

Serve the page first:

    python -m http.server 8001 --directory tests/e2e

Every string below was confirmed by driving `index.html` with Playwright directly, so
a mismatch is the agent, not the page.

| Task | What it exercises | Expected outcome |
| --- | --- | --- |
| 01 | Baseline, main frame only | Status reads `Main frame button was clicked` |
| 02 | Acting inside one iframe | Frame A status reads `Frame A button was clicked` |
| 03 | Scrolling a container inside a frame | Last row is `Scroll row 10 (bottom of Frame A)` |
| 04 | Fill, select, check, submit in one frame | Result reads `Submitted: Ada / blue / agreed=true` |
| 05 | Hopping A -> B -> A | Frame A status reads `Frame A button was clicked`; Frame B Name field holds `hello` |
| 06 | New tab from a frame, then back | New tab URL ends `index.html#opened-by-button`; counter still reads `count: 0` |
| 07 | New tab, return, keep acting in the frame | New tab URL ends `index.html#opened-from-frame-c`; final count is `count: 3` |
| 08 | Reaching a frame two levels deep | Paragraph reads `Nested frame, two levels deep.` |
| 09 | Frames stay isolated | Neither changed: Frame B still `not submitted`, main still `Main frame. Each box below is a separate iframe.` |
| 10 | Scrolling the page, not a container | Last line is `Bottom of the main page.` |

## Known gap

Tasks 02-09 all need the agent to act inside an iframe. `BrowserSession` tracks frames
and lists them in the snapshot under `<available-iframes>`, but `build_browser_tools`
exposes no tool for selecting one — `switch_tab` covers tabs only. Until a frame tool
exists, expect these to fail at the point where the agent has to leave the main frame.
That is the gap these tasks are meant to expose; 01 and 10 should pass regardless.
