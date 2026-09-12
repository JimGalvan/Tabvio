LOAD_TIMEOUT_MS = 6_000
EVALUATE_TIMEOUT_SECONDS = 8
OBSERVE_BUDGET_SECONDS = 30
BROWSER_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
VIEWPORT_WIDTH = 1365
VIEWPORT_HEIGHT = 768
FRAME_WIDTH = 960
FRAME_HEIGHT = 540
FRAME_QUALITY = 55
FRAME_QUALITY_TAKEOVER = 80
OBSERVE_ATTEMPTS = 3
PAYMENT_HANDOFF_SIGNAL_KINDS = frozenset({"card-autocomplete", "pay-button"})

CONTROL_KEYS = frozenset(
    {
        "Enter",
        "Tab",
        "Backspace",
        "Delete",
        "Escape",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "Control+A",
        "Meta+A",
    }
)
MAX_CONTROL_SCROLL_PIXELS = 2_000
