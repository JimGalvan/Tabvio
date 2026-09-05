# Expected outcomes

The `.txt` files hold nothing but the task text so they can be fed to the agent
verbatim. The answers live here instead, so a task file never leaks its own answer
into the prompt.

Serve the pages first:

    python -m http.server 8001 --directory tests/e2e

Every string below was confirmed by driving the pages with Playwright directly, so a
mismatch is the agent, not the pages.

| Task | What it exercises | Expected outcome |
| --- | --- | --- |
| 01 | Baseline, one page load | Page code is `HOME-7F3` |
| 02 | Two hops in the same tab, query string carries state | Item code `ITEM-BETA-822`, URL ends `item.html?id=beta` |
| 03 | Link with `target="_blank"`, then back to the first tab | New tab shows `ITEM-GAMMA-193`; first tab still `CATALOG-2B9` |
| 04 | Form submit as navigation, plus a page that fills in late | Confirmation reads `Order confirmed for Ada, size large, gift wrap yes.` |
| 05 | Full round trip, form to confirmation to home | Confirmation reads `Order confirmed for Bo, size small, gift wrap no.`; lands on `home.html` with code `HOME-7F3` |
| 06 | Counting tabs and reading each one | Two tabs: original `HOME-7F3`, new one `CATALOG-2B9` |
| 07 | New tab from `window.open`, switch there and back | New tab `ITEM-BETA-822`; first tab still `ITEM-ALPHA-514` |
| 08 | Three tabs, acting in the second to create the third | `ITEM-ALPHA-514`, `ITEM-BETA-822`, `ITEM-GAMMA-193` |
| 09 | Tabs stay isolated | New tab reads `count: 0`; first tab still reads `count: 3` |
| 10 | Four hops in one tab, returning to a page already visited | `ITEM-ALPHA-514` then `ITEM-BETA-822`, all in one tab |

## Notes

- Task 04 is the one to watch for the load detector in `navigate_and_observe`. The
  confirmation line reads `Processing the order, please wait.` for the first 1.5
  seconds and only then becomes the sentence above. An agent that snapshots once and
  answers immediately reports the wrong line.
- Tasks 03, 06, 07 and 08 need `switch_tab`. Tab ids come out of the snapshot as
  `tab:0`, `tab:1` and so on, in the order the tabs were registered.
- Task 09 fails in a telling way if the agent reads the wrong tab: the counter is a
  plain page variable, so a freshly opened tab always starts at `count: 0`.
- `item.html` with no `?id=` shows `no item selected` and item code `none`. Handy as a
  quick check that the agent really followed a link rather than guessing the URL.
