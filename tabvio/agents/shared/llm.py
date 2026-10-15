from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from tabvio.config import read_langchain_model_setting


def build_strong_model() -> BaseChatModel:
    model = read_langchain_model_setting("TABVIO_STRONG_MODEL", "openai:gpt-5.6-luna")
    if model.startswith("openai:"):
        return init_chat_model(model, reasoning_effort="none")
    return init_chat_model(model)


def build_fast_model() -> BaseChatModel:
    model = read_langchain_model_setting("TABVIO_FAST_MODEL", "openai:gpt-5-nano")
    if model.startswith("openai:"):
        return init_chat_model(model, reasoning_effort="low")
    return init_chat_model(model)
