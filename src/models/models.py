import os
from dotenv import load_dotenv
from strands.models.anthropic import AnthropicModel
from strands.models.bedrock import BedrockModel
from strands.models.openai import OpenAIModel
from strands.models.ollama import OllamaModel
from strands.models.writer import WriterModel
from strands.models.gemini import GeminiModel


# Load environment variables
load_dotenv()

# ============================================================================
# ANTHROPIC MODEL
# https://docs.claude.com/en/docs/about-claude/models/overview#legacy-models
# ============================================================================

def anthropic_model(api_key: str = os.getenv("ANTHROPIC_API_KEY"),
    model_id: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 4000,
    temperature: float = 1,
    thinking: bool = True,
    budget_tokens: int = 1024) -> AnthropicModel:
    """
    List of Anthropic models
    Args:
        api_key: The API key to use (default: os.getenv("ANTHROPIC_API_KEY")
        model_id: The model ID to use (default: claude-haiku-4-5-20251001)
        max_tokens: The maximum number of tokens to generate (default: 2000 max: 64000)
        temperature: The temperature to use (default: 1)
        thinking: Whether to use thinking (default: False)
        budget_tokens: The budget tokens to use (default: 1000)
    Returns:
        AnthropicModel

    Available models:
    - claude-haiku-4-5-20251001 - 200k context - 64k max_output tokens - input $1/M - output $5/M - Reasoning yes
    - claude-sonnet-4-5-20250929 - 200k context (1M beta) - 64k max_output tokens - input $3/M - output $15/M - Reasoning yes
    - claude-sonnet-4-20250514 - 200k context (1M beta) - 64k max_output tokens - input $3/M - output $15/M - Reasoning yes
    - claude-3-7-sonnet-20250219 - 200k context - 64k max_output tokens - input $3/M - output $15/M - Reasoning yes
    - claude-3-5-haiku-20241022 - 200k context - 8k max_output tokens - input $0.80/M - output $4/M - Reasoning no
    """
    if thinking:
        if budget_tokens >= max_tokens:
            raise ValueError("Budget tokens cannot be greater than max tokens")
        thinking = {
            "type": "enabled",
            "budget_tokens": budget_tokens
        }
    else:
        thinking = {
            "type": "disabled",
        }

    return AnthropicModel(
        client_args={
            "api_key": api_key,
        },
        max_tokens=max_tokens,
        model_id=model_id,
        params={
            "temperature": temperature,
            "thinking": thinking
        }
    )

# ============================================================================
# OPENAI MODEL
# https://platform.openai.com/docs/models
# ============================================================================

def openai_model(api_key: str = os.getenv("OPENAI_API_KEY"),
    model_id: str = "gpt-5-mini-2025-08-07",
    max_tokens: int = 16000,
    temperature: float = 1,
    reasoning_effort: str = "medium") -> OpenAIModel:
    """
    List of OpenAI models
    Args:
        api_key: The API key to use (default: os.getenv("OPENAI_API_KEY")
        model_id: The model ID to use (default: gpt-5-mini-2025-08-07)
        max_tokens: The maximum number of tokens to generate (default: 16000 max: 16000)
        temperature: The temperature to use (default: 1)
        reasoning_effort: The reasoning effort to use (default: "low")
    Returns:
        OpenAIModel

    Available models:
    - gpt-5-2025-08-07 - 400k context - 128K max_output tokens - input $1.25/M - output $10/M - Reasoning yes
    - gpt-4.1-2025-04-14 - 1M context - 32K max_output tokens - input $2/M - output $8/M - Reasoning no
    - o4-mini-2025-04-16 - 200k context - 100k max_output tokens - input $1.10/M - output $4.40/M - Reasoning yes
    - gpt-5-mini-2025-08-07 - 400k context - 128K max_output tokens - input $0.25/M - output $2/M - Reasoning yes
    - gpt-5-nano-2025-08-07 - 400k context - 128K max_output tokens - input $0.05/M - output $0.40/M - Reasoning yes
    - gpt-4o-2024-11-20 - 128k context - 16k max_output tokens - input $2.50/M - output $1.25/M - Reasoning no
    - gpt-5-pro-2025-10-06 - 400k context - 272K max_output tokens - input $1.25/M - output $120/M - Reasoning yes - slower
    - o4-mini-deep-research-2025-06-26 - 200k context - 100k max_output tokens - input $2/M - output $8/M - Reasoning yes
    """
    return OpenAIModel(
        client_args={
            "api_key": api_key,
        },
        model_id=model_id,
        params={
            "max_completion_tokens": max_tokens,
            "temperature": temperature,
            "reasoning_effort": reasoning_effort,
        }
    )


# ============================================================================
# OLLAMA MODEL
# https://ollama.com/library
# ============================================================================

def ollama_model(host: str = os.getenv("OLLAMA_HOST"),
    model_id: str = "qwen3:4b",
    max_tokens: int = 2000,
    temperature: float = 1,
    ) -> OllamaModel:
    """
    List of Ollama models
    Args:
        host: The host to use (default: os.getenv("OLLAMA_HOST")
        model_id: The model ID to use (default: qwen3:4b)
        max_tokens: The maximum number of tokens to generate (default: 2000 max: 128000)
        temperature: The temperature to use (default: 1)
    Returns:
        OllamaModel

    Available models:
    - qwen3:4b - 260k context - 128K max_output tokens
    - llama3.1:latest - 131k context - 128K max_output tokens
    - gemma3n:e4b (does not support tools) - 32k context - 8K max_output tokens
    - nomic-embed-text:latest (embedding model) - 2k context
    """
    if model_id == "qwen3:4b":
        max_tokens = 128000
    elif model_id == "llama3.1:latest":
        max_tokens = 128000
    elif model_id == "gemma3n:e4b":
        max_tokens = 8000
    else:
        raise ValueError(f"Model ID {model_id} not supported")

    if model_id == "gemma3n:e4b":
        print("NOTE: Tools are not supported with model.")
    return OllamaModel(
        host=host,
        model_id=model_id,
        max_tokens=max_tokens,
        temperature=temperature,
    )


# ============================================================================
# WRITER MODEL
# https://writer.com/library
# ============================================================================
def writer_model(api_key: str = os.getenv("WRITER_API_KEY"),
    model_id: str = "palmyra-x5",
    max_tokens: int = 2000,
    temperature: float = 1,
) -> WriterModel:
    """
    List of Writer models
    Args:
        api_key: The API key to use (default: os.getenv("WRITER_API_KEY")
        model_id: The model ID to use (default: palmyra-x5)
        max_tokens: The maximum number of tokens to generate (default: 2000 max: 2000)
        temperature: The temperature to use (default: 1)
    Returns:
        WriterModel

    Available models:
    - palmyra-x5 - 1M context - 32K max_output tokens - input $0.60/M - output $6.00/M
    - palmyra-x4 - 128k context - 32k max_output tokens - input $2.50/M - output $10/M
    - palmyra-fin - 128k context - 32k max_output tokens - input $5/M - output $12/M
    - palmyra-med - 32k context - 8k max_output tokens - input $5/M - output $12/M
    - palmyra-creative - 128k context - 32k max_output tokens - input $5/M - output $12/M
    """
    return WriterModel(
        client_args={"api_key": api_key},
        model_id=model_id,
        max_tokens=max_tokens,
        temperature=temperature,
    )


# ============================================================================
# GEMINI MODEL
# https://ai.google.dev/gemini-api/docs/models
# ============================================================================
def gemini_model(api_key: str = os.getenv("GOOGLE_API_KEY"),
    model_id: str = "gemini-3-flash-preview",
    max_tokens: int = 8192,
    temperature: float = 1,
    top_p: float = 0.95,
    top_k: int = 40,
    thinking: bool = False,
    budget_tokens: int = 1024,
    **kwargs) -> GeminiModel:
    """
    List of Gemini models
    Args:
        api_key: The API key to use (default: os.getenv("GOOGLE_API_KEY")
        model_id: The model ID to use (default: gemini-3-flash-preview)
        max_tokens: The maximum number of tokens to generate (default: 8192)
        temperature: The temperature to use (default: 1)
        top_p: The top_p to use (default: 0.95)
        top_k: The top_k to use (default: 40)
        thinking: Whether to enable thinking/reasoning mode (default: False)
        budget_tokens: The budget for thinking tokens (default: 1024)
        **kwargs: Additional model parameters (e.g. aspect_ratio for image gen)
    Returns:
        GeminiModel

    Available models:
    - gemini-3-pro-preview - 1M context - 65k max_output tokens - input $2/M - output $12/M (tiered >200k)
    - gemini-3-flash-preview - 1M context - 65k max_output tokens - input $0.50/M - output $3.00/M
    - gemini-3-pro-image-preview (nano banana) - Input $2/M - Output (Text) $12/M - Output (Image) $120/M
    
    Note: Gemini 3 Pro Image Input is 560 tokens per image. 
    Image output consumes: 1K/2K (1120 tokens, ~$0.134) or 4K (2000 tokens, ~$0.24).
    Gemini 3 Paid Tier ensures data privacy (No training). 
    Grounding with Google Search billing starts Jan 5, 2026.
    Context Caching for Flash: $0.05/M processing + $1.00/M/hour storage.
    """
    params = {
        "max_output_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        **kwargs
    }

    if thinking:
        params["thinking_config"] = {"include_thoughts": True}
        if budget_tokens:
            params["max_output_tokens"] = max_tokens + budget_tokens

    return GeminiModel(
        client_args={
            "api_key": api_key,
        },
        model_id=model_id,
        params=params
    )


# ============================================================================
# AMAZON BEDROCK MODEL
# https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html
# ============================================================================
def bedrock_model(
    model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region_name: str = os.getenv("AWS_REGION", "us-east-1"),
    max_tokens: int = 4096,
    temperature: float = 1.0,
    top_p: float = 0.9,
    stop_sequences: list[str] | None = None,
    thinking: bool = False,
    budget_tokens: int = 10000,
    extended_context: bool = False,
) -> BedrockModel:
    """
    Create an Amazon Bedrock model with support for multiple providers.

    Args:
        model_id: The Bedrock model ID (default: us.anthropic.claude-sonnet-4-5-20250929-v1:0)
        region_name: AWS region (default: AWS_REGION env var or us-east-1)
        max_tokens: Maximum tokens to generate (default: 4096)
        temperature: Temperature for sampling (default: 1.0)
        top_p: Top-p sampling parameter (default: 0.9)
        stop_sequences: Optional list of stop sequences
        thinking: Enable extended thinking for Anthropic models (default: False)
        budget_tokens: Token budget for thinking when enabled (default: 10000)
        extended_context: Enable 1M context beta for supported Anthropic models (default: False)

    Returns:
        BedrockModel

    Available models by provider:

    ANTHROPIC (Claude):
    - us.anthropic.claude-sonnet-4-5-20250929-v1:0 - 200k context (1M beta) - input $3/M - output $15/M
    - us.anthropic.claude-sonnet-4-20250514-v1:0 - 200k context (1M beta) - input $3/M - output $15/M
    - us.anthropic.claude-3-5-sonnet-20241022-v2:0 - 200k context - input $3/M - output $15/M
    - us.anthropic.claude-3-5-haiku-20241022-v1:0 - 200k context - input $0.80/M - output $4/M
    - us.anthropic.claude-3-opus-20240229-v1:0 - 200k context - input $15/M - output $75/M

    META (Llama):
    - us.meta.llama4-scout-17b-instruct-v1:0 - 512k context - input $0.27/M - output $0.35/M
    - us.meta.llama4-maverick-17b-instruct-v1:0 - 512k context - input $0.27/M - output $0.35/M
    - us.meta.llama3-3-70b-instruct-v1:0 - 128k context - input $0.72/M - output $0.72/M
    - meta.llama3-1-405b-instruct-v1:0 - 128k context - input $2.40/M - output $2.40/M

    MISTRAL:
    - mistral.mistral-large-2407-v1:0 - 128k context - input $2/M - output $6/M
    - mistral.mistral-small-2409-v1:0 - 32k context - input $0.10/M - output $0.30/M

    AMAZON (Nova):
    - amazon.nova-pro-v1:0 - 300k context - input $0.80/M - output $3.20/M
    - amazon.nova-lite-v1:0 - 300k context - input $0.06/M - output $0.24/M
    - amazon.nova-micro-v1:0 - 128k context - input $0.035/M - output $0.14/M
    - amazon.nova-premier-v1:0 - 1M context - input $2.50/M - output $12.50/M

    COHERE:
    - cohere.command-r-plus-v1:0 - 128k context - input $2.50/M - output $10/M
    - cohere.command-r-v1:0 - 128k context - input $0.15/M - output $0.60/M

    AI21:
    - ai21.jamba-1-5-large-v1:0 - 256k context - input $2/M - output $8/M
    - ai21.jamba-1-5-mini-v1:0 - 256k context - input $0.20/M - output $0.40/M

    Note: Requires AWS credentials configured via:
    - Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    - AWS credentials file (~/.aws/credentials)
    - IAM role (if running on AWS)

    Extended thinking and 1M context are only supported on Anthropic models.
    """
    is_anthropic = "anthropic" in model_id.lower()

    additional_params = {
        "temperature": temperature,
        "top_p": top_p,
    }

    if stop_sequences:
        additional_params["stop_sequences"] = stop_sequences

    # Anthropic-specific features
    if is_anthropic:
        if thinking:
            if budget_tokens >= max_tokens:
                raise ValueError("budget_tokens must be less than max_tokens")
            additional_params["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget_tokens,
            }

    # Extended context (1M beta) for supported Anthropic models
    additional_headers = {}
    if extended_context and is_anthropic:
        additional_headers["anthropic-beta"] = "extended-context-1m-2025-04-14"

    return BedrockModel(
        model_id=model_id,
        region_name=region_name,
        max_tokens=max_tokens,
        additional_request_fields=additional_params if additional_params else None,
        additional_headers=additional_headers if additional_headers else None,
    )