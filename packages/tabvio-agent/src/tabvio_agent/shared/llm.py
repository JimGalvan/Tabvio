from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from tabvio_agent.shared import constants


def build_strong_model(model: str) -> BaseChatModel:
    return _build_model(model, "strong_model", constants.STRONG_MODEL_REASONING_EFFORT)


def build_fast_model(model: str) -> BaseChatModel:
    return _build_model(model, "fast_model", constants.FAST_MODEL_REASONING_EFFORT)


def _build_model(model: str, setting: str, reasoning_effort: str) -> BaseChatModel:
    model = model.strip()
    provider, separator, model_id = model.partition(":")
    if not provider.strip() or not separator or not model_id.strip():
        raise RuntimeError(f"{setting} must use provider:model format (for example openai:gpt-5-nano)")
    if model.startswith(constants.OPENAI_PROVIDER_PREFIX):
        return init_chat_model(model, reasoning_effort=reasoning_effort)
    return init_chat_model(model)
