from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from tabvio.agents.shared import constants
from tabvio.config import read_langchain_model_setting


def build_strong_model() -> BaseChatModel:
    return _build_model(constants.STRONG_MODEL_SETTING, constants.DEFAULT_STRONG_MODEL, constants.STRONG_MODEL_REASONING_EFFORT)


def build_fast_model() -> BaseChatModel:
    return _build_model(constants.FAST_MODEL_SETTING, constants.DEFAULT_FAST_MODEL, constants.FAST_MODEL_REASONING_EFFORT)


def _build_model(setting: str, default: str, reasoning_effort: str) -> BaseChatModel:
    model = read_langchain_model_setting(setting, default)
    if model.startswith(constants.OPENAI_PROVIDER_PREFIX):
        return init_chat_model(model, reasoning_effort=reasoning_effort)
    return init_chat_model(model)
