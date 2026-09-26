"""Supported sourcing models, provider selection, and client construction."""

# API clients for the two providers.
import anthropic
# OpenAI Responses client.
import openai
# Credential presence without exposing values.
import os

# Existing Anthropic defaults.
from .agent import MODEL, ESCALATION_MODEL
# Invalid provider, model, or mode selections.
from .errors import InputError

# Models verified for the sourcing tools.
MODELS = {
    "anthropic": (MODEL, ESCALATION_MODEL),
    "openai": ("gpt-6-sol", "gpt-6-luna", "gpt-6-astra"),
}
# Default retry stays within the chosen provider.
RETRIES = {"anthropic": ESCALATION_MODEL, "openai": "gpt-6-astra"}


def provider_of(model: str) -> str:
    """Identify a model's provider, including historical Claude log entries."""
    # Historical Claude models remain usable in learning reports.
    return "openai" if model.startswith("gpt-") else "anthropic"


def select_models(provider: str, model: str | None = None, retry: str | None = None) -> tuple[str, str]:
    """Resolve and validate a provider's first-pass and retry models."""
    # Reject unknown providers before inspecting defaults.
    if provider not in MODELS:
        raise InputError(f"unknown provider: {provider}")
    # Select the provider defaults when no model was requested.
    first, second = model or MODELS[provider][0], retry or RETRIES[provider]
    # Never silently route an incompatible model to another API.
    if first not in MODELS[provider] or second not in MODELS[provider]:
        raise InputError(f"{provider} models: {', '.join(MODELS[provider])}")
    # Resolved pair.
    return first, second


def validate_mode(provider: str, live: bool) -> None:
    """Reject unsupported execution modes without submitting a request."""
    # OpenAI hosted web research is implemented only through live Responses.
    if provider == "openai" and not live:
        raise InputError("OpenAI sourcing requires --live; OpenAI web-search batch execution is not implemented.")


def create_client(provider: str):
    """Build the selected API client using only its own credentials."""
    # Environment variable for the selected provider.
    key = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    # Give a useful CLI error before constructing the SDK client.
    if not os.environ.get(key):
        raise InputError(f"Set {key} for {provider} sourcing.")
    # One shared asynchronous client for the pass.
    return openai.AsyncOpenAI() if provider == "openai" else anthropic.AsyncAnthropic()
