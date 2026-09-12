from pathlib import Path

from dotenv import load_dotenv
from strands.models import BedrockModel
from strands.models.bedrock import CacheConfig
from strands.models.openai import OpenAIModel

from tabvio.config import (
    read_aws_region_setting,
    read_fast_model_setting,
    read_model_provider_setting,
    read_strong_model_setting,
)

load_dotenv(dotenv_path=Path(__file__).resolve().parents[3] / ".env", override=True)

OPENAI_STRONG_MODEL = "gpt-5.6-luna"
OPENAI_FAST_MODEL = "gpt-5-nano"

# Cross-region inference profiles; Bedrock rejects the bare model ids. Sonnet 5
# and the Opus models answer "not available for this account", so the strong
# model is Sonnet 4.6. Every model needs the Anthropic use case form submitted
# for the account before it will answer at all.
BEDROCK_STRONG_MODEL = "us.anthropic.claude-sonnet-4-6"
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
    # The observe-decide-act loop resends the system prompt and every tool schema
    # each turn, so caching saves more than the model tier does. strict_tools stays
    # off because the browser step plan is a discriminated union and Bedrock's
    # strict mode rejects the oneOf schema that produces.
    return BedrockModel(
        model_id=model_id,
        region_name=read_aws_region_setting(),
        cache_config=CacheConfig(strategy="auto"),
        strict_tools=False,
    )
