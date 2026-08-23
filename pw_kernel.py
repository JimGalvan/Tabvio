"""ipykernel launcher that keeps a ProactorEventLoop on Windows.

ipykernel forces WindowsSelectorEventLoopPolicy at startup, and a Selector loop
cannot spawn subprocesses -- so Playwright's Node driver fails with
NotImplementedError. ipykernel only downgrades when the policy's type *is*
exactly WindowsProactorEventLoopPolicy, so a subclass passes through untouched.
pyzmq handles a Proactor loop via tornado's AddThreadSelectorEventLoop.
"""

import asyncio
import os
import sys

if sys.platform.startswith("win"):

    class _ProactorPolicy(asyncio.WindowsProactorEventLoopPolicy):
        """Subclassed so ipykernel's `type(...) is` check does not match."""

    asyncio.set_event_loop_policy(_ProactorPolicy())

# Behave like `python -m ipykernel_launcher`: drop this script's directory and
# make the working directory importable instead.
sys.path[0] = os.getcwd()

from ipykernel import kernelapp  # noqa: E402

kernelapp.launch_new_instance()
