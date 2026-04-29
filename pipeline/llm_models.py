"""Centralized OpenRouter model registry.

All LLM model identifiers and pricing live here so swaps are explicit.
Add a new entry to PRICING when introducing a new model.
"""

import os

EXTRACTION_MODEL = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")

DISCOVERY_QUERY_MODEL = os.environ.get(
    "OPENROUTER_DISCOVERY_MODEL", "anthropic/claude-sonnet-4.6"
)

# (input_per_token, output_per_token) in USD
PRICING: dict[str, tuple[float, float]] = {
    "google/gemini-2.5-flash": (0.10 / 1_000_000, 0.40 / 1_000_000),
    "anthropic/claude-sonnet-4.6": (3.00 / 1_000_000, 15.00 / 1_000_000),
}


def get_pricing(model: str) -> tuple[float, float]:
    """Return (input_per_token, output_per_token). Unknown models cost 0."""
    return PRICING.get(model, (0.0, 0.0))
