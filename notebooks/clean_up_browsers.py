"""Kill leftover Playwright processes.
    python notebooks/clean_up_browsers.py            # kill them
    python notebooks/clean_up_browsers.py --dry-run  # just list them
From a notebook cell:
    %run clean_up_browsers.py
"""

import argparse
import os
import sys
from pathlib import Path

import psutil

GRACE_SECONDS = 5


def _driver_dir() -> Path | None:
    try:
        import playwright
    except ImportError:
        return None
    return Path(playwright.__file__).resolve().parent / "driver"


def _browsers_dir() -> Path | None:
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if custom and custom != "0":
        return Path(custom).resolve()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        return Path(base).resolve() / "ms-playwright" if base else None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _roots() -> list[Path]:
    return [p for p in (_driver_dir(), _browsers_dir()) if p and p.is_dir()]


def find_processes() -> list[psutil.Process]:
    """Every running process whose executable sits inside a Playwright root."""
    roots = _roots()
    if not roots:
        return []

    me = os.getpid()
    found = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        if proc.pid == me:
            continue
        exe = proc.info.get("exe")
        if not exe:
            continue
        try:
            exe_path = Path(exe).resolve()
        except OSError:
            continue
        if any(exe_path.is_relative_to(root) for root in roots):
            found.append(proc)
    return found


def cleanup(dry_run: bool = False) -> int:
    for root in _roots():
        print(f"looking in {root}")

    procs = find_processes()
    if not procs:
        print("nothing to clean up")
        return 0

    for proc in procs:
        try:
            print(f"  {proc.pid:>7}  {proc.name()}")
        except psutil.NoSuchProcess:
            pass

    if dry_run:
        print(f"\n{len(procs)} process(es) found -- dry run, nothing killed")
        return 0

    for proc in procs:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    gone, alive = psutil.wait_procs(procs, timeout=GRACE_SECONDS)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if alive:
        psutil.wait_procs(alive, timeout=GRACE_SECONDS)

    print(f"\nkilled {len(procs)} process(es)")
    return len(procs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list the processes without killing them",
    )
    cleanup(dry_run=parser.parse_args().dry_run)


if __name__ == "__main__":
    main()
