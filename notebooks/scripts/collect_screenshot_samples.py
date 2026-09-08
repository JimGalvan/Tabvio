"""Collect screenshot-pair samples in parallel and score them.

    python notebooks/scripts/collect_screenshot_samples.py
    python notebooks/scripts/collect_screenshot_samples.py --concurrency 6
    python notebooks/scripts/collect_screenshot_samples.py --repeat 5 --only idle
    python notebooks/scripts/collect_screenshot_samples.py --wait 15
    python notebooks/scripts/collect_screenshot_samples.py --out data/samples/run1.json

Each sample is a pair of screenshots of the same page. `idle` samples wait
without touching anything, so any difference is the page moving on its own.
`action` samples do something in between, so the difference is (mostly) ours.

Headed is the default on purpose: headless Chromium throttles animation, which
silently zeroes out exactly what the idle samples are trying to measure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from playwright.async_api import Page, async_playwright
from skimage.metrics import structural_similarity as ssim

VIEWPORT = {"width": 1280, "height": 800}
SHOT = {"full_page": False, "type": "webp", "quality": 40}
CHANGE_THRESHOLD = 20  # per-channel difference we count as "this pixel moved"

DEFAULT_SETTLE = 2.0  # seconds to let a page settle before the first shot
DEFAULT_WAIT = 5.0  # seconds between the two shots of an idle sample
SAMPLE_TIMEOUT = 90.0  # hard ceiling per sample, seconds
NAV_TIMEOUT = 30_000  # playwright default_timeout, milliseconds

# Chrome throttles timers, rAF and rendering in windows it thinks nobody is
# looking at. With several windows open at once that would reintroduce the very
# problem headless causes, so switch the throttling off.
LAUNCH_ARGS = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def decode_pair(img1_bytes: bytes, img2_bytes: bytes) -> tuple[np.ndarray, np.ndarray]:
    a = np.array(Image.open(BytesIO(img1_bytes)).convert("RGB"))
    b = np.array(Image.open(BytesIO(img2_bytes)).convert("RGB"))
    return a, b


def screenshot_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(ssim(a, b, channel_axis=2, data_range=255))


def mean_abs_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Average absolute difference per channel, 0-255."""
    return float(cv2.absdiff(a, b).mean())


def changed_pixel_ratio(a: np.ndarray, b: np.ndarray, threshold: int = CHANGE_THRESHOLD) -> float:
    """Fraction of pixels that moved by more than `threshold` on any channel."""
    return float(np.any(cv2.absdiff(a, b) > threshold, axis=2).mean())


def score_pair(shot1: bytes, shot2: bytes, threshold: int = CHANGE_THRESHOLD) -> dict:
    a, b = decode_pair(shot1, shot2)
    if a.shape != b.shape:
        raise ValueError(f"screenshot sizes differ: {a.shape} vs {b.shape}")
    return {
        "ssim": screenshot_similarity(a, b),
        "abs_diff": mean_abs_diff(a, b),
        "pix_ratio": changed_pixel_ratio(a, b, threshold),
        "d_bytes": abs(len(shot1) - len(shot2)),
        "bytes1": len(shot1),
        "bytes2": len(shot2),
    }


# --------------------------------------------------------------------------
# sample registry
# --------------------------------------------------------------------------

Collector = Callable[[Page, "Timing"], Awaitable[tuple[bytes, bytes]]]


@dataclass(frozen=True)
class Timing:
    settle: float
    wait: float


@dataclass(frozen=True)
class Sample:
    name: str
    idle: bool
    collect: Collector
    local: bool = False  # needs a server on localhost, skipped unless asked for


SAMPLES: list[Sample] = []

# When several browsers compete for CPU, a screenshot takes longer to come
# back and the real interval between the two shots drifts away from --wait.
# For idle samples that interval *is* the independent variable, so record when
# each shot actually happened instead of trusting the sleep. A ContextVar gives
# every sample task its own list without threading an argument through all the
# collectors.
SHOT_TIMES: ContextVar[list[float]] = ContextVar("shot_times")

# The page's text, grabbed at the same two instants as the screenshots, so a
# text-based method can be judged on exactly the same moments as a pixel-based
# one instead of getting a wider window to look at.
SHOT_TEXTS: ContextVar[list[dict]] = ContextVar("shot_texts")


# inner_text("body") is a poor stand-in for what the agent actually observes:
# it returns text that is scrolled out of view, and it cannot see form state at
# all, so a scroll, a filled field or a picked dropdown look like nothing
# happened. This snapshot keeps to what is on screen and records control state.
VISIBLE_SNAPSHOT_JS = """
() => {
  const out = [];
  const els = document.body ? document.body.querySelectorAll('*') : [];
  let budget = 3000;  // big pages must not stall the capture
  for (const el of els) {
    if (budget-- <= 0) break;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
    const tag = el.tagName;
    if (tag === 'INPUT') {
      out.push(`input|${el.type}|${el.checked ? 'on' : 'off'}|${el.value}`);
    } else if (tag === 'SELECT') {
      out.push(`select|${el.value}`);
    } else if (tag === 'TEXTAREA') {
      out.push(`textarea|${el.value}`);
    } else {
      const own = Array.from(el.childNodes)
        .filter(n => n.nodeType === 3)
        .map(n => n.textContent.trim())
        .filter(Boolean).join(' ');
      if (own) out.push(own);
    }
  }
  return out.join('\\n');
}
"""


async def shot(page: Page) -> bytes:
    image = await page.screenshot(**SHOT)
    SHOT_TIMES.get().append(time.monotonic())
    # both captures, so the naive one and the richer one are judged on the
    # exact same instants rather than across two collections
    try:
        text = await page.inner_text("body", timeout=5000)
    except Exception:
        text = ""  # a page that will not give up its text is data, not an error
    try:
        snapshot = await page.evaluate(VISIBLE_SNAPSHOT_JS)
    except Exception:
        snapshot = ""
    SHOT_TEXTS.get().append({"text": text, "snapshot": snapshot})
    return image


def idle_site(name: str, url: str) -> None:
    """Register an idle sample: load, settle, shoot, wait, shoot."""

    async def collect(page: Page, timing: Timing) -> tuple[bytes, bytes]:
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(timing.settle)
        first = await shot(page)
        await asyncio.sleep(timing.wait)
        return first, await shot(page)

    SAMPLES.append(Sample(name=name, idle=True, collect=collect))


def action(name: str, *, local: bool = False):
    """Register an action sample. The decorated function returns both shots."""

    def wrap(fn: Collector) -> Collector:
        SAMPLES.append(Sample(name=name, idle=False, collect=fn, local=local))
        return fn

    return wrap


# --- idle -----------------------------------------------------------------

idle_site("example-static", "https://example.com/")
idle_site("wikipedia-idle", "https://en.wikipedia.org/wiki/Web_browser")
idle_site("nasa-home", "https://www.nasa.gov/")
idle_site("bbc-news", "https://www.bbc.com/news")
idle_site("openweathermap-london", "https://openweathermap.org/city/5128581")
idle_site("coinbase-btc-price", "https://www.coinbase.com/price/bitcoin")
idle_site("timeanddate-worldclock", "https://www.timeanddate.com/worldclock/")
idle_site("flightradar24", "https://www.flightradar24.com/")
idle_site("clock-zone", "https://clock.zone/")
idle_site("wikipedia-recent-changes", "https://en.wikipedia.org/wiki/Special:RecentChanges")
idle_site("time-gov", "https://www.time.gov/")
idle_site("time.is", "https://time.is/")
idle_site("yahoo-finance-btc", "https://finance.yahoo.com/quote/BTC-USD/")
idle_site("coinmarketcap", "https://coinmarketcap.com/currencies/bitcoin/")
idle_site("espn-home", "https://www.espn.com/")
idle_site("webgl-aquarium", "https://webglsamples.org/aquarium/aquarium.html")
idle_site("apod-nasa", "https://apod.nasa.gov/apod/astropix.html")
idle_site("mdn-css-idle", "https://developer.mozilla.org/en-US/docs/Web/CSS")
idle_site("python-docs-idle", "https://docs.python.org/3/")
idle_site("wikipedia-main-idle", "https://en.wikipedia.org/wiki/Main_Page")
idle_site("cloudflare-status", "https://www.cloudflarestatus.com/")
idle_site("cnn-home", "https://www.cnn.com/")
idle_site("marketwatch", "https://www.marketwatch.com/")
idle_site("webgl-blob", "https://webglsamples.org/blob/blob.html")


# --- action ---------------------------------------------------------------

@action("e-commerce", local=True)
async def _ecommerce(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("http://localhost:8001/shop/product.html")
    first = await shot(page)
    await page.locator("//*[@id='add-to-cart']").click()
    await asyncio.sleep(2)
    return first, await shot(page)


@action("hackernews")
async def _hackernews(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://news.ycombinator.com/newest")
    first = await shot(page)
    await page.goto("https://news.ycombinator.com/front")
    await asyncio.sleep(2)
    return first, await shot(page)


@action("wikipedia-search-suggestions")
async def _wiki_suggestions(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://en.wikipedia.org/wiki/Main_Page")
    first = await shot(page)
    await page.locator("input[name='search']").first.click()
    await page.keyboard.type("playwright")
    await asyncio.sleep(2)
    return first, await shot(page)


@action("wikipedia-scroll")
async def _wiki_scroll(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://en.wikipedia.org/wiki/Web_browser")
    first = await shot(page)
    await page.mouse.wheel(0, 1200)
    await asyncio.sleep(2)
    return first, await shot(page)


@action("cross-site-navigation")
async def _cross_site(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://example.com/")
    first = await shot(page)
    await page.goto("https://www.iana.org/help/example-domains")
    await asyncio.sleep(1)
    return first, await shot(page)


@action("dynamic-loading")
async def _dynamic_loading(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://the-internet.herokuapp.com/dynamic_loading/1")
    first = await shot(page)
    await page.locator("#start button").click()
    await asyncio.sleep(6)
    return first, await shot(page)


@action("checkbox-toggle")
async def _checkbox(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://the-internet.herokuapp.com/checkboxes")
    first = await shot(page)
    await page.locator("#checkboxes input").first.check()
    await asyncio.sleep(1)
    return first, await shot(page)


@action("login-form-fill")
async def _login_fill(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://the-internet.herokuapp.com/login")
    first = await shot(page)
    await page.locator("#username").fill("tomsmith")
    await page.locator("#password").fill("SuperSecretPassword!")
    await asyncio.sleep(1)
    return first, await shot(page)


@action("login-submit-success")
async def _login_submit(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://the-internet.herokuapp.com/login")
    await page.locator("#username").fill("tomsmith")
    await page.locator("#password").fill("SuperSecretPassword!")
    first = await shot(page)
    await page.locator("button[type='submit']").click()
    await asyncio.sleep(3)
    return first, await shot(page)


@action("infinite-scroll")
async def _infinite_scroll(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://the-internet.herokuapp.com/infinite_scroll")
    first = await shot(page)
    await page.keyboard.press("End")
    await asyncio.sleep(3)
    return first, await shot(page)


@action("hover-reveal-caption")
async def _hover(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://the-internet.herokuapp.com/hovers")
    first = await shot(page)
    await page.locator(".figure").first.hover()
    await asyncio.sleep(1)
    return first, await shot(page)


@action("github-issues-tab")
async def _github_issues(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://github.com/microsoft/playwright", wait_until="domcontentloaded")
    await asyncio.sleep(2)
    first = await shot(page)
    await page.get_by_role("link", name="Issues").first.click()
    await asyncio.sleep(3)
    return first, await shot(page)


@action("hn-open-comments")
async def _hn_comments(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://news.ycombinator.com/", wait_until="domcontentloaded")
    first = await shot(page)
    await page.locator("a:has-text('comments')").first.click()
    await asyncio.sleep(3)
    return first, await shot(page)


@action("duckduckgo-search")
async def _ddg(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://html.duckduckgo.com/html/", wait_until="domcontentloaded")
    first = await shot(page)
    await page.locator("input[name='q']").first.fill("playwright python")
    await page.keyboard.press("Enter")
    await asyncio.sleep(3)
    return first, await shot(page)


@action("python-org-search")
async def _python_org(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://www.python.org/", wait_until="domcontentloaded")
    await asyncio.sleep(2)
    first = await shot(page)
    await page.locator("#id-search-field").fill("asyncio")
    await page.keyboard.press("Enter")
    await asyncio.sleep(4)
    return first, await shot(page)


@action("mdn-theme-menu")
async def _mdn_theme(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://developer.mozilla.org/en-US/docs/Web/HTML", wait_until="domcontentloaded")
    await asyncio.sleep(2)
    first = await shot(page)
    await page.get_by_role("button", name="Theme").first.click()
    await asyncio.sleep(2)
    return first, await shot(page)


@action("wikipedia-portal-typeahead")
async def _wiki_portal(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://www.wikipedia.org/", wait_until="domcontentloaded")
    await asyncio.sleep(1)
    first = await shot(page)
    await page.locator("#searchInput").fill("screenshot")
    await asyncio.sleep(2)
    return first, await shot(page)


@action("wikihow-scroll")
async def _wikihow(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://www.wikihow.com/Main-Page", wait_until="domcontentloaded")
    await asyncio.sleep(2)
    first = await shot(page)
    await page.mouse.wheel(0, 1500)
    await asyncio.sleep(2)
    return first, await shot(page)


@action("bootstrap-modal-open")
async def _bootstrap_modal(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://getbootstrap.com/docs/5.3/components/modal/",
                    wait_until="domcontentloaded")
    await asyncio.sleep(2)
    first = await shot(page)
    await page.get_by_role("button", name="Launch demo modal").first.click()
    await asyncio.sleep(2)
    return first, await shot(page)


@action("pypi-release-history")
async def _pypi_releases(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://pypi.org/project/playwright/", wait_until="domcontentloaded")
    await asyncio.sleep(1)
    first = await shot(page)
    # the page ships a mobile and a desktop nav, so pin the visible one
    await page.locator("a[href='#history']:visible").first.click()
    await asyncio.sleep(3)
    return first, await shot(page)


@action("add-element")
async def _add_element(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://the-internet.herokuapp.com/add_remove_elements/")
    first = await shot(page)
    await page.get_by_role("button", name="Add Element").click()
    await asyncio.sleep(1)
    return first, await shot(page)


@action("dropdown-select")
async def _dropdown(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://the-internet.herokuapp.com/dropdown")
    first = await shot(page)
    await page.locator("#dropdown").select_option("2")
    await asyncio.sleep(1)
    return first, await shot(page)


@action("pypi-search")
async def _pypi_search(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://pypi.org/", wait_until="domcontentloaded")
    await asyncio.sleep(1)
    first = await shot(page)
    await page.locator("input#search").first.fill("playwright")
    await page.keyboard.press("Enter")
    await asyncio.sleep(4)
    return first, await shot(page)


@action("gutenberg-search")
async def _gutenberg(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://www.gutenberg.org/", wait_until="domcontentloaded")
    await asyncio.sleep(1)
    first = await shot(page)
    await page.locator("input[name='query']").first.fill("dune")
    await page.keyboard.press("Enter")
    await asyncio.sleep(4)
    return first, await shot(page)


@action("github-branch-dropdown")
async def _github_branches(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://github.com/python/cpython", wait_until="domcontentloaded")
    await asyncio.sleep(3)
    first = await shot(page)
    await page.get_by_role("button", name="main branch").first.click()
    await asyncio.sleep(2)
    return first, await shot(page)


@action("stackexchange-typeahead")
async def _stackexchange(page: Page, timing: Timing) -> tuple[bytes, bytes]:
    await page.goto("https://stackexchange.com/", wait_until="domcontentloaded")
    await asyncio.sleep(1)
    first = await shot(page)
    await page.locator("input[name='q']").first.fill("playwright")
    await asyncio.sleep(3)
    return first, await shot(page)


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------

@dataclass
class Result:
    name: str
    idle: bool
    run: int
    status: str = "ok"
    error: str = ""
    elapsed: float = 0.0
    gap: float = 0.0  # measured seconds between the two shots
    metrics: dict = field(default_factory=dict)

    def as_row(self) -> dict:
        return {"name": self.name, "idle": self.idle, "run": self.run,
                "status": self.status, "error": self.error,
                "elapsed": round(self.elapsed, 2), "gap": round(self.gap, 2),
                **self.metrics}


async def run_sample(browser, sample: Sample, run: int, timing: Timing,
                     threshold: int, shots_dir: Path | None) -> Result:
    result = Result(name=sample.name, idle=sample.idle, run=run)
    started = time.monotonic()
    context = None
    SHOT_TIMES.set([])
    SHOT_TEXTS.set([])
    try:
        context = await browser.new_context(viewport=VIEWPORT)
        page = await context.new_page()
        page.set_default_timeout(NAV_TIMEOUT)
        shot1, shot2 = await asyncio.wait_for(
            sample.collect(page, timing), timeout=SAMPLE_TIMEOUT
        )
        stamps = SHOT_TIMES.get()
        if len(stamps) >= 2:
            result.gap = stamps[-1] - stamps[0]
        # scoring is CPU-bound; keep it off the event loop so the other
        # samples in flight are not stalled by it
        result.metrics = await asyncio.to_thread(score_pair, shot1, shot2, threshold)
        if shots_dir is not None:
            stem = f"{sample.name}__run{run}"
            (shots_dir / f"{stem}__1.webp").write_bytes(shot1)
            (shots_dir / f"{stem}__2.webp").write_bytes(shot2)
            texts = SHOT_TEXTS.get()
            if len(texts) >= 2:
                for suffix, captured in (("1", texts[0]), ("2", texts[-1])):
                    (shots_dir / f"{stem}__{suffix}.json").write_text(
                        json.dumps(captured), encoding="utf-8")
    except TimeoutError:
        result.status = "timeout"
        result.error = f"exceeded {SAMPLE_TIMEOUT:.0f}s"
    except Exception as exc:  # noqa: BLE001 - one bad site must not kill the run
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
    result.elapsed = time.monotonic() - started
    status = "ok" if result.status == "ok" else result.status.upper()
    print(f"  [{status:7s}] {sample.name} (run {run}, {result.elapsed:.1f}s)", flush=True)
    return result


async def collect_all(samples: list[Sample], args: argparse.Namespace) -> list[Result]:
    timing = Timing(settle=args.settle, wait=args.wait)
    shots_dir = Path(args.save_shots) if args.save_shots else None
    if shots_dir is not None:
        shots_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(args.concurrency)
    results: list[Result] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless, args=LAUNCH_ARGS)

        async def guarded(sample: Sample, run: int) -> Result:
            async with semaphore:
                return await run_sample(browser, sample, run, timing,
                                        args.threshold, shots_dir)

        tasks = [guarded(sample, run)
                 for run in range(1, args.repeat + 1)
                 for sample in samples]
        results = await asyncio.gather(*tasks)
        await browser.close()

    return list(results)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def print_table(results: list[Result]) -> None:
    ok = [r for r in results if r.status == "ok"]
    header = (f"{'sample':30s} {'kind':7s} {'run':>3s} {'gap':>6s} {'ssim':>8s} "
              f"{'abs_diff':>9s} {'pix_ratio':>10s} {'d_bytes':>9s}")
    print("\n" + header)
    print("-" * len(header))
    for r in sorted(ok, key=lambda r: (not r.idle, r.name, r.run)):
        m = r.metrics
        print(f"{r.name:30s} {'idle' if r.idle else 'action':7s} {r.run:3d} "
              f"{r.gap:6.2f} {m['ssim']:8.4f} {m['abs_diff']:9.3f} "
              f"{m['pix_ratio']:10.5f} {m['d_bytes']:9d}")

    broken = [r for r in results if r.status != "ok"]
    if broken:
        print(f"\n{len(broken)} sample(s) did not produce a pair:")
        for r in sorted(broken, key=lambda r: r.name):
            print(f"  {r.name:30s} run {r.run}  {r.status}: {r.error}")


def describe(rows: list[Result], label: str) -> None:
    if not rows:
        print(f"{label}: no samples")
        return
    print(f"{label} (n={len(rows)})")
    for key, fmt in (("ssim", ".4f"), ("abs_diff", ".3f"), ("pix_ratio", ".5f")):
        vals = sorted(r.metrics[key] for r in rows)
        med = statistics.median(vals)
        print(f"  {key:9s} min={vals[0]:{fmt}}  median={med:{fmt}}  max={vals[-1]:{fmt}}")


def print_summary(results: list[Result]) -> None:
    ok = [r for r in results if r.status == "ok"]
    if not ok:
        return
    idle_rows = [r for r in ok if r.idle]
    action_rows = [r for r in ok if not r.idle]
    print()
    describe(idle_rows, "idle")
    print()
    describe(action_rows, "action-driven")

    if idle_rows and action_rows:
        floor = min(r.metrics["pix_ratio"] for r in action_rows)
        noisy = sorted((r for r in idle_rows if r.metrics["pix_ratio"] > floor),
                       key=lambda r: -r.metrics["pix_ratio"])
        print(f"\nIdle pages noisier than the quietest action (pix_ratio {floor:.5f}):")
        for r in noisy:
            print(f"  {r.name:30s} pix_ratio={r.metrics['pix_ratio']:.5f}")
        if not noisy:
            print("  none")


def print_gap_drift(results: list[Result], expected: float, tolerance: float = 0.15) -> None:
    """Idle noise is a function of elapsed time, so a stretched gap inflates it.

    Under concurrency the screenshot call itself queues behind other browsers,
    and the real interval drifts past --wait. Flag it rather than let it quietly
    contaminate the numbers.
    """
    drifted = [r for r in results
               if r.status == "ok" and r.idle and r.gap > expected * (1 + tolerance)]
    if not drifted:
        return
    print(f"\nIdle gap ran long (asked for {expected:.1f}s) - these are inflated:")
    for r in sorted(drifted, key=lambda r: -r.gap):
        print(f"  {r.name:30s} gap={r.gap:5.2f}s  (+{(r.gap / expected - 1) * 100:.0f}%)"
              f"  pix_ratio={r.metrics['pix_ratio']:.5f}")
    print("  Lower --concurrency for a clean noise floor, or divide by gap.")


def print_variance(results: list[Result], repeat: int) -> None:
    """With repeats, per-page spread matters more than any single number."""
    if repeat < 2:
        return
    ok = [r for r in results if r.status == "ok"]
    by_name: dict[str, list[float]] = {}
    for r in ok:
        by_name.setdefault(r.name, []).append(r.metrics["pix_ratio"])

    print(f"\npix_ratio across {repeat} runs (is the noise floor even stable?)")
    print(f"{'sample':30s} {'n':>2s} {'min':>10s} {'median':>10s} {'max':>10s} {'max/min':>9s}")
    for name, vals in sorted(by_name.items(), key=lambda kv: -statistics.median(kv[1])):
        if len(vals) < 2:
            continue
        lo, hi = min(vals), max(vals)
        spread = f"{hi / lo:9.1f}" if lo > 0 else "      inf"
        print(f"{name:30s} {len(vals):2d} {lo:10.5f} {statistics.median(vals):10.5f} "
              f"{hi:10.5f} {spread}")


def write_json(results: list[Result], args: argparse.Namespace, path: Path) -> None:
    payload = {
        "collected_at": datetime.now(UTC).isoformat(),
        "headless": args.headless,
        "viewport": VIEWPORT,
        "settle": args.settle,
        "wait": args.wait,
        "repeat": args.repeat,
        "concurrency": args.concurrency,
        "change_threshold": args.threshold,
        "rows": [r.as_row() for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {len(results)} rows to {path}")


# --------------------------------------------------------------------------

def select_samples(args: argparse.Namespace) -> list[Sample]:
    chosen = SAMPLES
    if args.only == "idle":
        chosen = [s for s in chosen if s.idle]
    elif args.only == "action":
        chosen = [s for s in chosen if not s.idle]
    if args.sample:
        wanted = set(args.sample)
        chosen = [s for s in chosen if s.name in wanted]
        missing = wanted - {s.name for s in chosen}
        if missing:
            sys.exit(f"unknown sample(s): {', '.join(sorted(missing))}")
    if not args.include_local:
        chosen = [s for s in chosen if not s.local]
    return chosen


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--concurrency", type=int, default=5,
                   help="samples in flight at once (default: 5)")
    p.add_argument("--repeat", type=int, default=1,
                   help="collect each sample this many times (default: 1)")
    p.add_argument("--headless", action="store_true",
                   help="faster, but suppresses animation and flattens idle noise")
    p.add_argument("--settle", type=float, default=DEFAULT_SETTLE,
                   help=f"seconds before the first idle shot (default: {DEFAULT_SETTLE})")
    p.add_argument("--wait", type=float, default=DEFAULT_WAIT,
                   help=f"seconds between idle shots (default: {DEFAULT_WAIT})")
    p.add_argument("--threshold", type=int, default=CHANGE_THRESHOLD,
                   help=f"per-channel change threshold (default: {CHANGE_THRESHOLD})")
    p.add_argument("--only", choices=("all", "idle", "action"), default="all")
    p.add_argument("--sample", action="append", metavar="NAME",
                   help="collect only this sample (repeatable)")
    p.add_argument("--include-local", action="store_true",
                   help="include samples needing a server on localhost")
    p.add_argument("--save-shots", metavar="DIR",
                   help="also write both screenshots of every pair into DIR")
    p.add_argument("--out", metavar="PATH", help="write results as JSON")
    p.add_argument("--list", action="store_true", help="list samples and exit")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.list:
        for s in SAMPLES:
            tag = "idle  " if s.idle else "action"
            local = "  (needs localhost)" if s.local else ""
            print(f"{tag}  {s.name}{local}")
        print(f"\n{len(SAMPLES)} samples registered")
        return

    samples = select_samples(args)
    if not samples:
        sys.exit("no samples selected")

    total = len(samples) * args.repeat
    mode = "headless" if args.headless else "headed"
    print(f"collecting {total} pair(s) from {len(samples)} sample(s), "
          f"{mode}, concurrency {args.concurrency}")

    started = time.monotonic()
    results = asyncio.run(collect_all(samples, args))
    elapsed = time.monotonic() - started

    print_table(results)
    print_summary(results)
    print_gap_drift(results, args.wait)
    print_variance(results, args.repeat)

    ok = sum(1 for r in results if r.status == "ok")
    print(f"\n{ok}/{len(results)} pairs collected in {elapsed:.1f}s")

    if args.out:
        write_json(results, args, Path(args.out))


if __name__ == "__main__":
    main()
