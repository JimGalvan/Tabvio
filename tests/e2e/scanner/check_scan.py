"""Score the page scanner against the pages in this directory.

What the agent can see is a number, not a feeling, so this runs the real script in
a real browser over a labelled corpus and prints where it was wrong. Grow the
corpus by saving a page here and adding it to EXPECTED below.

    python tests/e2e/scanner/check_scan.py

Each page is one defect the scanner used to have. A page passes when every label
under "present" is in the snapshot, no label under "absent" is, the attributes
under "attrs" say what state the control is in, and every coordinate the scan
hands out lands on the element it claims to describe. That last check is the one
that catches silent dead clicks, where the agent is told it clicked and nothing
happened.

Reaching into the session's privates is deliberate: the point is to test the real
scan path, and switching frames has no public entry point yet.
"""

import asyncio
import functools
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tabvio.browser.constants import VIEWPORT_HEIGHT, VIEWPORT_WIDTH  # noqa: E402
from tabvio.browser.formatting import format_scan_for_llm  # noqa: E402
from tabvio.browser.session import BrowserSession  # noqa: E402

PAGES = Path(__file__).resolve().parent
SITE_ROOT = PAGES.parent

EXPECTED = {
    "shadow-dom.html": {
        "present": [
            "Light DOM button",
            "Add to cart in shadow DOM",
            "Increase quantity two roots deep",
        ],
        "hits": {
            "Add to cart in shadow DOM": "product-card >>> #shadow-button",
            "Increase quantity two roots deep":
                "product-card >>> quantity-stepper >>> #nested-shadow-button",
        },
    },
    "fold.html": {
        "present": [
            "Dismiss the promo",
            "Straddles the fold",
            "Centre under the promo bar",
        ],
        # The promo bar sits on top of it, so a person cannot click it either.
        "absent": ["Buried under the promo bar"],
        "hits": {
            "Straddles the fold": "#straddles",
            "Centre under the promo bar": "#centre-covered",
        },
    },
    "hidden-subtrees.html": {
        "present": ["Really visible button", "Delivery date"],
        "absent": [
            "Menu item in a faded wrapper",
            "Duplicate in an aria-hidden mirror",
            "Button in an inert subtree",
        ],
        "hits": {"Delivery date": "#partly-covered"},
    },
    "named-controls.html": {
        "present": [
            "Card number",
            "Shopping cart",
            "Delete this item",
            "Help centre",
            "Email me offers",
            "Standard delivery",
            "Express delivery",
            "Size",
            "Save my details",
            "Place the order",
        ],
        # The name belongs on the control, not on a second entry of its own.
        "absent": [
            "Search products by name and category",
            "express",
            "standard",
        ],
        "attrs": {
            "Card number": ["type=text"],
            "Email me offers": ["unchecked"],
            "Express delivery": ["checked"],
            "Size": ["selected=9"],
            "Save my details": ["role=checkbox", "checked"],
            "Place the order": ["role=button", "disabled"],
            "Country": ["role=combobox", "collapsed"],
        },
        "tags": {
            "Card number": "input",
            "Email me offers": "input",
            "Size": "select",
        },
    },
    "mega-nav.html": {
        "present": ["Place order"],
        "in_prompt": ["Place order"],
    },
    "sensitive-values.html": {
        "present": ["Card number", "Email", "Search"],
        "absent": [
            "4242424242424242",
            "jane.doe@example.com",
            "session=abcd1234efgh5678",
            "token=secret",
        ],
        "attrs": {"Card number": ["filled"], "Email": ["filled"]},
        "escapes_markup": True,
    },
}


def serve(root: Path) -> tuple[socketserver.TCPServer, int]:
    """Serve the corpus over http; iframes and relative links need a real origin."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(root),
    )
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


HIT_TEST = """({x, y, selector}) => {
    const ascend = (node) => {
        if (node.parentElement) return node.parentElement;
        const root = node.getRootNode();
        return root instanceof ShadowRoot ? root.host : null;
    };

    const resolve = (path) => {
        let scope = document;
        let element = null;
        for (const part of path.split('>>>')) {
            element = scope.querySelector(part.trim());
            if (!element) return null;
            scope = element.shadowRoot || element;
        }
        return element;
    };

    let hit = document.elementFromPoint(x, y);
    while (hit && hit.shadowRoot) {
        const deeper = hit.shadowRoot.elementFromPoint(x, y);
        if (!deeper || deeper === hit) break;
        hit = deeper;
    }
    if (!hit) return {reached: false, hit: 'nothing'};

    const target = resolve(selector);
    if (!target) return {reached: false, hit: 'nothing matching ' + selector};

    for (let node = hit; node; node = ascend(node)) {
        if (node === target) return {reached: true, hit: hit.tagName};
    }
    return {reached: false, hit: hit.id || hit.className || hit.tagName};
}"""


def find(elements: list[dict], needle: str) -> dict | None:
    for element in elements:
        if needle in element["text"]:
            return element

    return None


def find_anywhere(elements: list[dict], needle: str) -> dict | None:
    """Secrets leak through attributes as readily as through labels."""
    for element in elements:
        if needle in element["text"] or needle in element["attrs"]:
            return element

    return None


def check_labels(elements: list[dict], case: dict) -> list[str]:
    problems = []

    for needle in case.get("present", []):
        if find(elements, needle) is None:
            problems.append(f"missing {needle!r}")

    for needle in case.get("absent", []):
        found = find_anywhere(elements, needle)
        if found is not None:
            problems.append(f"should not report {needle!r} (as <{found['tag']}>)")

    for needle, tag in case.get("tags", {}).items():
        found = find(elements, needle)
        if found is not None and found["tag"] != tag:
            problems.append(f"{needle!r} reported as <{found['tag']}>, expected <{tag}>")

    for needle, wanted in case.get("attrs", {}).items():
        found = find(elements, needle)
        if found is None:
            problems.append(f"missing {needle!r}, so its attributes cannot be checked")
            continue

        for attribute in wanted:
            if attribute not in found["attrs"]:
                problems.append(
                    f"{needle!r} attrs {found['attrs']!r} is missing {attribute!r}"
                )

    return problems


async def check_coordinates(
    session: BrowserSession, elements: list[dict], case: dict
) -> list[str]:
    """Every coordinate must be on screen and must land on what it describes."""
    problems = []
    frame = session._active_frame()

    for element in elements:
        on_screen = (
            0 <= element["cx"] < VIEWPORT_WIDTH and 0 <= element["cy"] < VIEWPORT_HEIGHT
        )
        if not on_screen:
            problems.append(
                f"{element['text'][:40]!r} points at "
                f"({element['cx']:.0f}, {element['cy']:.0f}), off a "
                f"{VIEWPORT_WIDTH}x{VIEWPORT_HEIGHT} viewport"
            )

    for needle, selector in case.get("hits", {}).items():
        found = find(elements, needle)
        if found is None:
            continue

        result = await frame.evaluate(
            HIT_TEST,
            {"x": found["cx"], "y": found["cy"], "selector": selector},
        )
        if not result["reached"]:
            problems.append(
                f"{needle!r} points at ({found['cx']:.0f}, {found['cy']:.0f}), "
                f"which hits {result['hit']} rather than {selector}"
            )

    return problems


def check_prompt(scan: dict, case: dict) -> list[str]:
    problems = []
    prompt = format_scan_for_llm(scan)

    for needle in case.get("in_prompt", []):
        if needle not in prompt:
            problems.append(f"{needle!r} never reaches the prompt")

    if case.get("escapes_markup"):
        if prompt.count("</page>") != 1:
            problems.append("the page can close the <page> block itself")
        if "&lt;/page&gt;" not in prompt:
            problems.append("angle brackets written by the page are not escaped")

    return problems


async def check_page(session: BrowserSession, url: str, case: dict) -> list[str]:
    await session.attempt_navigate_and_observe(url)
    scan = json.loads(await session._scan_page())
    elements = scan["elements"]

    return (
        check_labels(elements, case)
        + await check_coordinates(session, elements, case)
        + check_prompt(scan, case)
    )


async def check_scroll_pane(session: BrowserSession, base: str) -> list[str]:
    """Content in an inner scroll pane has to be both reported and reachable."""
    problems = []
    await session.attempt_navigate_and_observe(f"{base}/scanner/scroll-pane.html")
    scan = json.loads(await session._scan_page())

    if scan["pagesBelow"] <= 0:
        problems.append(
            f"pagesBelow is {scan['pagesBelow']}, so the agent is told there is "
            "nothing below while 48 messages are"
        )

    await session.scroll(1)
    scrolled = json.loads(await session._scan_page())
    if find(scrolled["elements"], "Open message 20") is None:
        problems.append("scrolling one page did not move the inner pane")

    return problems


async def check_tall_frame(
    session: BrowserSession, base: str, other_origin: str = ""
) -> list[str]:
    """Inside a frame taller than the viewport, only what a person can see counts."""
    problems = []
    await session.attempt_navigate_and_observe(f"{base}/scanner/tall-frame.html")
    page = session._require_page()

    if other_origin:
        await page.evaluate(
            "origin => document.querySelector('iframe').src ="
            " origin + '/scanner/tall-frame-inner.html'",
            other_origin,
        )
        await page.wait_for_load_state("networkidle")

    frames = [frame for frame in page.frames if frame.name == "checkout"]
    if not frames:
        return ["the checkout frame never loaded"]

    session._iframe = frames[0]
    scan = json.loads(await session._scan_page())
    elements = scan["elements"]

    if find(elements, "Near the top of the frame") is None:
        problems.append("missing the button at the top of the frame")

    deep = find(elements, "Below the page viewport")
    if deep is not None:
        horizontal, vertical = await session._page_coordinates(deep["cx"], deep["cy"])
        if vertical >= VIEWPORT_HEIGHT:
            problems.append(
                f"reports a button at page y {vertical:.0f}, below the "
                f"{VIEWPORT_HEIGHT}px viewport, so clicking it does nothing"
            )

    session._iframe = page.main_frame
    return problems


def report(name: str, problems: list[str]) -> int:
    print(f"{'FAIL' if problems else 'ok  '}  {name}")
    for problem in problems:
        print(f"        {problem}")

    return len(problems)


async def main() -> int:
    server, port = serve(SITE_ROOT)
    base = f"http://127.0.0.1:{port}"
    session = BrowserSession(headless=True)
    failures = 0

    try:
        for name, case in EXPECTED.items():
            problems = await check_page(session, f"{base}/scanner/{name}", case)
            failures += report(name, problems)

        failures += report(
            "scroll-pane.html", await check_scroll_pane(session, base)
        )
        failures += report(
            "tall-frame.html", await check_tall_frame(session, base)
        )
        # localhost and 127.0.0.1 are separate origins, which is the shape a
        # hosted payment field has.
        failures += report(
            "tall-frame.html (cross-origin)",
            await check_tall_frame(session, base, f"http://localhost:{port}"),
        )
    finally:
        await session.close()
        server.shutdown()

    print(f"\n{failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
