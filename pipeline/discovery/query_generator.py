"""Generate Spanish search queries to discover new event sources.

MVP for stage (a) of the source-discovery pipeline:
  1. fetch existing source domains from the Momaverse API (for dedupe)
  2. ask an LLM (DISCOVERY_QUERY_MODEL) to produce N diverse Spanish queries
  3. validate output shape, raise on failure (no retries)
  4. write JSON to out/discovery_queries_<timestamp>.json for human review

No Firecrawl, no DB writes — this only emits queries.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import openai
from llm_models import DISCOVERY_QUERY_MODEL, get_pricing
from pydantic import BaseModel, Field, ValidationError

from .prompts import build_query_prompt

DISCOVERY_TIMEOUT = int(os.environ.get("DISCOVERY_TIMEOUT", "60"))
DEFAULT_N_QUERIES = 15
SOURCES_PAGE_SIZE = 500
DETAIL_CONCURRENCY = 10


class QueryValidationError(ValueError):
    """Raised when LLM output fails count, schema, or uniqueness checks."""


@dataclass
class DiscoveryConfig:
    city: str = "Buenos Aires"
    country: str = "Argentina"
    n_queries: int = DEFAULT_N_QUERIES
    api_url: str | None = None
    api_token: str | None = None
    model: str = DISCOVERY_QUERY_MODEL
    fetch_existing: bool = True


class DiscoveryQuery(BaseModel):
    query: str = Field(min_length=1)
    category: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    target_neighborhood: str | None = None


class DiscoveryQueryList(BaseModel):
    queries: list[DiscoveryQuery]


# ---------------------------------------------------------------------------
# Existing-source domain fetch
# ---------------------------------------------------------------------------


def _domain_of(url: str) -> str | None:
    try:
        netloc = urlparse(url).netloc.lower()
    except (ValueError, AttributeError):
        return None
    if not netloc:
        return None
    return netloc[4:] if netloc.startswith("www.") else netloc


async def _fetch_source_ids(client: httpx.AsyncClient, api_url: str) -> list[int]:
    resp = await client.get(
        f"{api_url}/api/v1/sources/", params={"limit": SOURCES_PAGE_SIZE}
    )
    resp.raise_for_status()
    payload = resp.json()
    items = payload.get("items") if isinstance(payload, dict) else payload
    return [item["id"] for item in (items or []) if "id" in item]


async def _fetch_source_urls(
    client: httpx.AsyncClient, api_url: str, source_id: int
) -> list[str]:
    resp = await client.get(f"{api_url}/api/v1/sources/{source_id}")
    resp.raise_for_status()
    detail = resp.json()
    urls_field = detail.get("urls") or []
    return [u.get("url") for u in urls_field if isinstance(u, dict) and u.get("url")]


async def fetch_existing_domains(api_url: str, api_token: str) -> list[str]:
    """Pull every source's URLs from the API and return unique domains."""
    headers = {"Authorization": f"Bearer {api_token}"}
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        source_ids = await _fetch_source_ids(client, api_url)
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def bounded(sid: int) -> list[str]:
            async with sem:
                return await _fetch_source_urls(client, api_url, sid)

        url_lists = await asyncio.gather(*[bounded(sid) for sid in source_ids])

    domains: set[str] = set()
    for urls in url_lists:
        for u in urls:
            d = _domain_of(u)
            if d:
                domains.add(d)
    return sorted(domains)


# ---------------------------------------------------------------------------
# LLM call + validation
# ---------------------------------------------------------------------------


def _make_client() -> openai.AsyncOpenAI:
    api_key = os.environ.get("OPENROUTER_CRAWLER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_CRAWLER_API_KEY env var is required")
    return openai.AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")


def validate_queries(
    payload: DiscoveryQueryList, expected_count: int
) -> DiscoveryQueryList:
    """Enforce count and uniqueness. Raises QueryValidationError on failure."""
    queries = payload.queries
    if len(queries) != expected_count:
        raise QueryValidationError(
            f"Expected {expected_count} queries, got {len(queries)}"
        )
    seen: set[str] = set()
    for q in queries:
        normalized = q.query.strip().lower()
        if normalized in seen:
            raise QueryValidationError(f"Duplicate query: {q.query!r}")
        seen.add(normalized)
    return payload


async def call_llm(
    client: openai.AsyncOpenAI, model: str, prompt: str
) -> tuple[DiscoveryQueryList, dict]:
    response = await client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
        timeout=DISCOVERY_TIMEOUT,
    )
    raw = response.choices[0].message.content or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise QueryValidationError(f"LLM returned non-JSON: {e}") from e
    try:
        parsed = DiscoveryQueryList.model_validate(data)
    except ValidationError as e:
        raise QueryValidationError(f"Schema mismatch: {e}") from e

    usage = getattr(response, "usage", None)
    usage_dict = {
        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }
    return parsed, usage_dict


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def generate_queries(
    config: DiscoveryConfig,
) -> tuple[DiscoveryQueryList, list[str], dict]:
    if config.fetch_existing and config.api_url and config.api_token:
        existing_domains = await fetch_existing_domains(
            config.api_url, config.api_token
        )
    else:
        existing_domains = []

    prompt = build_query_prompt(
        city=config.city,
        country=config.country,
        n_queries=config.n_queries,
        existing_domains=existing_domains,
    )

    client = _make_client()
    parsed, usage = await call_llm(client, config.model, prompt)
    validated = validate_queries(parsed, config.n_queries)
    return validated, existing_domains, usage


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------


def _cost_estimate(model: str, usage: dict) -> float:
    in_price, out_price = get_pricing(model)
    return (
        usage.get("input_tokens", 0) * in_price
        + usage.get("output_tokens", 0) * out_price
    )


def write_output(
    queries: DiscoveryQueryList,
    existing_domains: list[str],
    config: DiscoveryConfig,
    usage: dict,
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = out_dir / f"discovery_queries_{timestamp}.json"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": config.model,
        "city": config.city,
        "country": config.country,
        "n_queries": config.n_queries,
        "existing_domain_count": len(existing_domains),
        "usage": usage,
        "estimated_cost_usd": round(_cost_estimate(config.model, usage), 6),
        "queries": [q.model_dump() for q in queries.queries],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path
