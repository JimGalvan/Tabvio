"""Language-model configuration for Tabvio's browser agent."""

from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=True)

model = init_chat_model("openai:gpt-5-nano", reasoning_effort="low")
strong_model = init_chat_model("openai:gpt-5.6-terra", reasoning_effort="none")
