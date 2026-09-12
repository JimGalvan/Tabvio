from botocore.config import Config as BotocoreConfig
from dotenv import load_dotenv
from strands.models import BedrockModel
from strands.models.bedrock import CacheConfig
from strands.models.openai import OpenAIModel

from tabvio.config import (
    PROJECT_DIRECTORY,
    read_aws_region_setting,
    read_fast_model_setting,
    read_model_provider_setting,
    read_strong_model_setting,
)

load_dotenv(dotenv_path=PROJECT_DIRECTORY / ".env", override=True)

OPENAI_STRONG_MODEL = "gpt-5.6-luna"
OPENAI_FAST_MODEL = "gpt-5-nano"
BEDROCK_STRONG_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_FAST_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def build_strong_model():
    if read_model_provider_setting() == "bedrock":
        return _build_bedrock_model(read_strong_model_setting(BEDROCK_STRONG_MODEL))
    return OpenAIModel(
        model_id=read_strong_model_setting(OPENAI_STRONG_MODEL),
        params={"reasoning_effort": "none"},
    )


def build_fast_model():
    if read_model_provider_setting() == "bedrock":
        return _build_bedrock_model(read_fast_model_setting(BEDROCK_FAST_MODEL))
    return OpenAIModel(
        model_id=read_fast_model_setting(OPENAI_FAST_MODEL),
        params={"reasoning_effort": "low"},
    )


def _build_bedrock_model(model_id: str) -> BedrockModel:
    return BedrockModel(
        model_id=model_id,
        region_name=read_aws_region_setting(),
        cache_config=CacheConfig(strategy="auto"),
        strict_tools=False,
        boto_client_config=_bedrock_client_config(),
    )


def _bedrock_client_config() -> BotocoreConfig:
    return BotocoreConfig(
        connect_timeout=10,
        read_timeout=60,
        retries={"max_attempts": 3, "mode": "standard"},
    )
