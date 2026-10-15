from dotenv import load_dotenv

from tabvio.config import PROJECT_DIRECTORY

load_dotenv(dotenv_path=PROJECT_DIRECTORY / ".env", override=True)

from tabvio.server.routes import app

__all__ = ["app"]
