# Notebooks

## The metadata block

Every `.ipynb` ends with a `metadata` block that says which kernel it wants:

```json
"kernelspec": {
  "display_name": "Python 3 (Playwright)",
  "language": "python",
  "name": "tabvio-playwright"
}
```

Two kernels are registered in `.venv/share/jupyter/kernels/`:

| name | what it runs |
|---|---|
| `python3` | the normal `ipykernel_launcher` |
| `tabvio-playwright` | [`pw_kernel.py`](../pw_kernel.py) |

Use `tabvio-playwright` for any notebook that drives Playwright.

## Why the Playwright kernel exists

Playwright launches a Node subprocess. On Windows only asyncio's Proactor loop
can spawn subprocesses, and ipykernel forces the Selector loop at startup, so
`chromium.launch()` fails with `NotImplementedError`. `pw_kernel.py` subclasses
the Proactor policy to slip past ipykernel's exact-type check.

This only bites outside PyCharm — `jupyter lab`, `jupyter execute`, nbconvert.
PyCharm wraps the kernel itself and already keeps a Proactor loop, so Playwright
works there under either kernel.

## Changing the kernel

In PyCharm, use the kernel picker in the top right of the notebook editor.
Elsewhere, edit the `kernelspec` block above by hand.

## Note on pip

`.venv` is created by uv, which does not install pip. PyCharm reads a venv's
package list *by running pip inside it*, so without pip it thinks `notebook` is
missing, tries to install it, fails, and refuses to start Jupyter at all.
Fix: `uv pip install pip`. A `uv sync` will remove it again unless pip is added
to the `dev` group in `pyproject.toml`.

## Cleaning up stray browsers

A cell that dies before `browser.close()` leaves the Node driver and its browser
children running. [`clean_up_browsers.py`](clean_up_browsers.py) kills them:

```bash
python notebooks/clean_up_browsers.py --dry-run   # list them
python notebooks/clean_up_browsers.py             # kill them
```

It matches on executable path — the bundled driver `node`, and anything under
the Playwright browsers directory — so your own Chrome is never touched.
