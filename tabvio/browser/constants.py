LOAD_TIMEOUT_MS = 6_000
EVALUATE_TIMEOUT_SECONDS = 3
SCAN_BUDGET_SECONDS = 5
BROWSER_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
VIEWPORT_WIDTH = 1365
VIEWPORT_HEIGHT = 768
FRAME_WIDTH = 960
FRAME_HEIGHT = 540
FRAME_QUALITY = 55
FRAME_QUALITY_TAKEOVER = 80
OBSERVE_ATTEMPTS = 3
PAYMENT_HANDOFF_SIGNAL_KINDS = frozenset({"card-autocomplete", "pay-button"})

KEY_ALIASES = {
    "ctrl": "Control",
    "control": "Control",
    "cmd": "Meta",
    "command": "Meta",
    "meta": "Meta",
    "win": "Meta",
    "alt": "Alt",
    "option": "Alt",
    "shift": "Shift",
    "esc": "Escape",
    "escape": "Escape",
    "enter": "Enter",
    "return": "Enter",
    "tab": "Tab",
    "space": "Space",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
}

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
