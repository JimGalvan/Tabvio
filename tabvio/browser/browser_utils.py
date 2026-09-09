from pathlib import Path


class BrowserUtils:
    @staticmethod
    def get_js_script(name: str):
        script_path = Path(__file__).resolve().parent / "scripts" / name
        return script_path.read_text(encoding="utf-8")
