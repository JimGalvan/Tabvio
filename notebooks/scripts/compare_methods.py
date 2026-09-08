"""Score every change-detection method on the same dataset, side by side.

    # collect once, keeping the images
    python notebooks/scripts/collect_screenshot_samples.py \
        --save-shots data/samples/shots --out data/samples/run1.json

    # then compare methods as often as you like, no browsing needed
    python notebooks/scripts/compare_methods.py \
        --shots data/samples/shots --labels data/samples/run1.json

A method is one function that looks at a pair of screenshots and returns a
single number, where HIGHER means "more likely a real action, not the page
moving by itself". It does not need a threshold and does not decide anything.

The framework turns that number into one comparable score: given a random
action sample and a random idle sample, how often does the method rank the
action higher? 50% is a coin flip. 100% is perfect separation. That way every
method is judged the same way, whatever scale it happens to return.

Methods that need data this dataset does not contain (several frames, or the
agent's step history) declare it and are reported as blocked rather than
guessed at.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.stats import rankdata
from skimage.metrics import structural_similarity as ssim

CHANGE_THRESHOLD = 20
MIN_BLOB_PIXELS = 40  # ignore specks when counting change regions


# --------------------------------------------------------------------------
# what a method gets to look at
# --------------------------------------------------------------------------

@dataclass
class Pair:
    """One sample: two screenshots and what we know about them."""
    name: str
    idle: bool
    run: int
    raw1: bytes
    raw2: bytes
    text1: str | None = None
    text2: str | None = None
    snap1: str | None = None
    snap2: str | None = None
    _img1: np.ndarray | None = field(default=None, repr=False)
    _img2: np.ndarray | None = field(default=None, repr=False)
    _mask: np.ndarray | None = field(default=None, repr=False)

    @property
    def img1(self) -> np.ndarray:
        if self._img1 is None:
            self._img1 = np.array(Image.open(BytesIO(self.raw1)).convert("RGB"))
        return self._img1

    @property
    def img2(self) -> np.ndarray:
        if self._img2 is None:
            self._img2 = np.array(Image.open(BytesIO(self.raw2)).convert("RGB"))
        return self._img2

    @property
    def change_mask(self) -> np.ndarray:
        """Boolean map of which pixels moved. Computed once, reused by methods."""
        if self._mask is None:
            diff = cv2.absdiff(self.img1, self.img2)
            self._mask = np.any(diff > CHANGE_THRESHOLD, axis=2)
        return self._mask

    def gray(self, which: int) -> np.ndarray:
        img = self.img1 if which == 1 else self.img2
        return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


# --------------------------------------------------------------------------
# method registry
# --------------------------------------------------------------------------

@dataclass
class Method:
    category: str
    name: str
    blurb: str
    fn: Callable[[Pair], float] | None = None
    needs: str = ""  # non-empty means it cannot run on a pair dataset
    needs_text: bool = False


METHODS: list[Method] = []


def method(category: str, name: str, blurb: str, *, needs_text: bool = False):
    def wrap(fn):
        METHODS.append(Method(category, name, blurb, fn, needs_text=needs_text))
        return fn

    return wrap


def blocked(category: str, name: str, blurb: str, needs: str) -> None:
    """Register a method we cannot score yet, and say exactly what it wants."""
    METHODS.append(Method(category, name, blurb, None, needs))


# --- 1. whole-image scalars ------------------------------------------------

@method("whole-image", "ssim", "structural similarity of the two images")
def m_ssim(p: Pair) -> float:
    return 1.0 - float(ssim(p.img1, p.img2, channel_axis=2, data_range=255))


@method("whole-image", "abs_diff", "average absolute pixel difference")
def m_abs_diff(p: Pair) -> float:
    return float(cv2.absdiff(p.img1, p.img2).mean())


@method("whole-image", "pix_ratio", "share of pixels that moved past a threshold")
def m_pix_ratio(p: Pair) -> float:
    return float(p.change_mask.mean())


@method("whole-image", "d_bytes", "difference in compressed file size")
def m_d_bytes(p: Pair) -> float:
    return float(abs(len(p.raw1) - len(p.raw2)))


# --- 2. spatial ------------------------------------------------------------

def _blobs(mask: np.ndarray) -> tuple[int, np.ndarray]:
    """Group changed pixels into connected regions, dropping specks."""
    count, _, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8)
    if count <= 1:
        return 0, np.zeros((0,), dtype=np.int64)
    areas = stats[1:, cv2.CC_STAT_AREA]  # row 0 is the background
    areas = areas[areas >= MIN_BLOB_PIXELS]
    return len(areas), areas


@method("spatial", "largest-blob", "size of the single biggest changed region")
def m_largest_blob(p: Pair) -> float:
    _, areas = _blobs(p.change_mask)
    return float(areas.max() / p.change_mask.size) if len(areas) else 0.0


@method("spatial", "blob-count", "how many separate regions changed")
def m_blob_count(p: Pair) -> float:
    count, _ = _blobs(p.change_mask)
    return float(count)


@method("spatial", "concentration",
        "is the change one solid block, or scattered flecks?")
def m_concentration(p: Pair) -> float:
    _, areas = _blobs(p.change_mask)
    total = float(areas.sum())
    if total <= 0:
        return 0.0
    return float(areas.max() / total)


blocked("spatial", "frequency-mask",
        "ignore the areas a page always animates in",
        needs="several frames per page, not a single pair")

blocked("spatial", "click-region",
        "only look near the element we interacted with",
        needs="the coordinates of the element the agent acted on")


# --- 3. fingerprint --------------------------------------------------------

def _dhash(gray: np.ndarray, size: int = 8) -> np.ndarray:
    small = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    return (small[:, 1:] > small[:, :-1]).flatten()


@method("fingerprint", "phash-distance",
        "how many bits of a 64-bit visual fingerprint differ")
def m_phash(p: Pair) -> float:
    return float(np.count_nonzero(_dhash(p.gray(1)) != _dhash(p.gray(2))))


blocked("fingerprint", "visit-history",
        "spot the agent returning to a screen it has already seen",
        needs="a run's whole trajectory, not one isolated pair")


# --- 4. page text ----------------------------------------------------------
# Deliberately pair-only: these see the same two instants as every pixel
# method, never a run's history, so the comparison stays like-for-like.

def _line_counts(text: str) -> Counter:
    return Counter(line.strip() for line in text.splitlines() if line.strip())


@method("dom-text", "dom-changed", "did the page text change at all",
        needs_text=True)
def m_dom_changed(p: Pair) -> float:
    return 0.0 if (p.text1 or "") == (p.text2 or "") else 1.0


@method("dom-text", "dom-distance", "how much of the page text changed",
        needs_text=True)
def m_dom_distance(p: Pair) -> float:
    c1, c2 = _line_counts(p.text1 or ""), _line_counts(p.text2 or "")
    total = sum(c1.values()) + sum(c2.values())
    if total == 0:
        return 0.0
    shared = sum((c1 & c2).values())
    return 1.0 - (2.0 * shared / total)


@method("dom-text", "snap-changed",
        "did the on-screen text or any form control change", needs_text=True)
def m_snap_changed(p: Pair) -> float:
    return 0.0 if (p.snap1 or "") == (p.snap2 or "") else 1.0


@method("dom-text", "snap-distance",
        "how much of the on-screen text and form state changed", needs_text=True)
def m_snap_distance(p: Pair) -> float:
    c1, c2 = _line_counts(p.snap1 or ""), _line_counts(p.snap2 or "")
    total = sum(c1.values()) + sum(c2.values())
    if total == 0:
        return 0.0
    shared = sum((c1 & c2).values())
    return 1.0 - (2.0 * shared / total)


# --- 5. non-visual ---------------------------------------------------------

blocked("non-visual", "repeated-action",
        "the same tool fired at the same target N times running",
        needs="the agent's step history")

blocked("non-visual", "repeated-target",
        "the same element clicked over and over",
        needs="the agent's step history")


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def cut_accuracy(scores: list[float], is_action: list[bool]) -> float:
    """How often a single fixed threshold gets the answer right.

    Separation and thresholdability are not the same thing: a method can rank
    the two groups well overall and still have no clean place to cut, if one
    group is split into clusters that straddle the other. The threshold is
    picked without the sample being judged, so this is not self-graded.
    """
    x = np.asarray(scores, dtype=float)
    y = np.asarray(is_action, dtype=bool)
    if len(set(y.tolist())) < 2:
        return float("nan")
    correct = 0
    for i in range(len(x)):
        keep = np.ones(len(x), bool)
        keep[i] = False
        xt, yt = x[keep], y[keep]
        best_t, best_hits = None, -1
        for t in np.unique(xt):
            hits = int(((xt >= t) == yt).sum())
            if hits > best_hits:
                best_hits, best_t = hits, t
        correct += int((x[i] >= best_t) == y[i])
    return correct / len(x)


def separation_score(scores: list[float], is_action: list[bool]) -> float:
    """Chance this method ranks a random action above a random idle page.

    Equivalent to the area under the ROC curve, computed by ranking so that
    ties count as half a win instead of being silently broken.
    """
    n_action = sum(is_action)
    n_idle = len(is_action) - n_action
    if n_action == 0 or n_idle == 0:
        return float("nan")
    ranks = rankdata(scores)
    action_rank_sum = float(sum(r for r, a in zip(ranks, is_action) if a))
    return (action_rank_sum - n_action * (n_action + 1) / 2) / (n_action * n_idle)


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------

def load_pairs(shots_dir: Path, labels_path: Path) -> list[Pair]:
    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    pairs: list[Pair] = []
    missing = 0
    for row in payload.get("rows", []):
        if row.get("status") != "ok":
            continue
        stem = f"{row['name']}__run{row['run']}"
        f1 = shots_dir / f"{stem}__1.webp"
        f2 = shots_dir / f"{stem}__2.webp"
        if not (f1.exists() and f2.exists()):
            missing += 1
            continue
        captured = []
        for suffix in ("1", "2"):
            side = shots_dir / f"{stem}__{suffix}.json"
            captured.append(json.loads(side.read_text(encoding="utf-8"))
                            if side.exists() else {})
        pairs.append(Pair(name=row["name"], idle=bool(row["idle"]), run=row["run"],
                          raw1=f1.read_bytes(), raw2=f2.read_bytes(),
                          text1=captured[0].get("text"), text2=captured[1].get("text"),
                          snap1=captured[0].get("snapshot"),
                          snap2=captured[1].get("snapshot")))
    if missing:
        print(f"note: {missing} labelled sample(s) had no saved images", file=sys.stderr)
    return pairs


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def strength(score: float) -> float:
    """A method that ranks every idle page above every action is not useless -
    it is a perfect signal pointing the wrong way. Judge methods on how well
    they separate the two groups, and report the direction separately."""
    return max(score, 1.0 - score)


def report(results: dict[str, float], cuts: dict[str, float],
           pairs: list[Pair]) -> None:
    n_action = sum(1 for p in pairs if not p.idle)
    n_idle = len(pairs) - n_action
    print(f"\n{len(pairs)} pairs: {n_action} action, {n_idle} idle")
    print("sep   = how well it separates the two groups over all thresholds")
    print("cut   = how often ONE fixed threshold is right - the deployable number")
    print("dir   = '+' higher means action, '-' higher means idle (flip its sign to use)")
    print("share = this method's slice of its own category, ranked by cut")
    print("        (sep and cut disagree when a group splits into clusters that")
    print("         straddle the other - good ranking, nowhere clean to cut)\n")
    print(f"  {'method':20s} {'sep':>6s} {'cut':>6s}")

    categories = []
    for m in METHODS:
        if m.category not in categories:
            categories.append(m.category)

    winners: list[tuple[str, str, float]] = []

    for category in categories:
        members = [m for m in METHODS if m.category == category]
        runnable = [m for m in members if m.fn is not None and m.name in results]
        print(f"{category.upper()}")

        if runnable:
            # a share of what each method wins ABOVE a coin flip, so a category
            # of near-useless methods cannot look impressive by dividing itself up
            edges = {m.name: max(cuts[m.name] - 0.5, 0.0) for m in runnable}
            total_edge = sum(edges.values())
            for m in sorted(runnable, key=lambda m: -cuts[m.name]):
                raw = results[m.name]
                share = (edges[m.name] / total_edge * 100) if total_edge > 0 else 0.0
                direction = "+" if raw >= 0.5 else "-"
                print(f"  {m.name:20s} {strength(raw):6.1%} {cuts[m.name]:6.1%}  "
                      f"{direction}  {share:5.1f}%   {m.blurb}")
            best = max(runnable, key=lambda m: cuts[m.name])
            winners.append((category, best.name, cuts[best.name]))
            if total_edge <= 0:
                print("  (no method in this category beats a coin flip)")
            else:
                print(f"  -> best: {best.name}")

        for m in members:
            if m.fn is None:
                print(f"  {m.name:20s}     --     --       --    BLOCKED: needs {m.needs}")
        print()

    if winners:
        print("CATEGORY WINNERS (by cut - the number you could actually deploy)")
        for category, name, score in sorted(winners, key=lambda w: -w[2]):
            bar = "#" * round((max(score, 0.5) - 0.5) * 80)
            print(f"  {category + '/' + name:32s} {score:6.1%}  {bar}")
        print(f"  {'coin flip':32s} {0.5:6.1%}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--shots", help="directory of saved screenshot pairs")
    p.add_argument("--labels", help="the run's JSON from the collector")
    p.add_argument("--list", action="store_true", help="list methods and exit")
    args = p.parse_args()

    if args.list:
        for m in METHODS:
            state = f"BLOCKED (needs {m.needs})" if m.fn is None else "ready"
            print(f"{m.category:12s} {m.name:20s} {state}")
        return

    if not (args.shots and args.labels):
        sys.exit("--shots and --labels are required (or use --list)")

    pairs = load_pairs(Path(args.shots), Path(args.labels))
    if not pairs:
        sys.exit("no usable pairs found - did the collector run with --save-shots?")

    have_text = any(p_.text1 is not None for p_ in pairs)
    if not have_text:
        for m in METHODS:
            if m.needs_text:
                m.fn = None
                m.needs = "page text saved next to the shots - re-run the collector"

    is_action = [not p_.idle for p_ in pairs]
    results: dict[str, float] = {}
    cuts: dict[str, float] = {}
    for m in METHODS:
        if m.fn is None:
            continue
        scores = [m.fn(pair) for pair in pairs]
        results[m.name] = separation_score(scores, is_action)
        cuts[m.name] = cut_accuracy(scores, is_action)
        print(f"  scored {m.name}", flush=True)

    report(results, cuts, pairs)


if __name__ == "__main__":
    main()
