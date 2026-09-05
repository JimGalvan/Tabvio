# Nav test site

Five pages for exercising Tabvio's page navigation and tab handling. No iframes here
on purpose — for the iframe cases see `../index.html`.

Serve it from the same root as the other e2e page:

    python -m http.server 8001 --directory tests/e2e

Then point a run at `http://localhost:8001/nav/home.html`.

## The pages

Every page carries a `page code` near the top, so an answer says plainly which page
the agent was looking at.

- **home.html** (`HOME-7F3`) — links into the catalog and the order form, a link that
  opens the catalog in a new tab, a link that opens Home itself in a new tab, and a
  counter button whose value lives only in that tab.
- **catalog.html** (`CATALOG-2B9`) — three items. Alpha and Beta open in the same tab,
  Gamma opens in a new one.
- **item.html** (`ITEM-PAGE-6C1`) — reads `?id=alpha|beta|gamma` and shows that item's
  code (`ITEM-ALPHA-514`, `ITEM-BETA-822`, `ITEM-GAMMA-193`). A button opens the next
  item in a new tab, so tabs can be chained. With no `?id=` it says `no item selected`.
- **form.html** (`FORM-9A5`) — name, size and gift wrap. Submitting is a real GET
  navigation to the confirmation page.
- **confirm.html** (`CONFIRM-D4`) — shows `Processing the order, please wait.` for 1.5
  seconds, then the order summary built from the query string. A "Start over" link
  goes back to Home.

Every page has the same three-link nav bar, so any page can reach any other.

Things worth checking: following links across several pages in one tab, a query string
surviving the hop, a form submit counted as navigation, waiting for a page that fills
in after load, opening tabs from a link and from `window.open`, switching between two
or three tabs, and confirming a new tab does not inherit the first tab's state.

## Tasks

`tasks/` holds one task per `.txt` file, written the way a user would type it. Paste
one into a run as-is. `tasks/EXPECTED.md` says what each should produce and which part
of the browser layer it leans on.
