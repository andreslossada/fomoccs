"""Tests for discovery.query_generator."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery.prompts import build_query_prompt
from discovery.query_generator import (
    DiscoveryConfig,
    DiscoveryQuery,
    DiscoveryQueryList,
    QueryValidationError,
    _domain_of,
    call_llm,
    validate_queries,
    write_output,
)

# ---------------------------------------------------------------------------
# Domain extraction
# ---------------------------------------------------------------------------


class TestDomainOf:
    def test_strips_www(self):
        assert _domain_of("https://www.example.com/events") == "example.com"

    def test_keeps_subdomain(self):
        assert _domain_of("https://agenda.gob.ar/list") == "agenda.gob.ar"

    def test_handles_no_scheme(self):
        # urlparse without scheme returns empty netloc — should be None
        assert _domain_of("example.com/events") is None

    def test_lowercases(self):
        assert _domain_of("https://Example.COM/x") == "example.com"

    def test_returns_none_on_empty(self):
        assert _domain_of("") is None


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


class TestBuildQueryPrompt:
    def test_includes_city_and_country(self):
        prompt = build_query_prompt(
            "Buenos Aires", "Argentina", 15, ["foo.com", "bar.com"]
        )
        assert "Buenos Aires" in prompt
        assert "Argentina" in prompt
        assert "15" in prompt

    def test_includes_existing_domains(self):
        prompt = build_query_prompt("BA", "AR", 5, ["foo.com", "bar.com"])
        assert "foo.com" in prompt
        assert "bar.com" in prompt

    def test_excludes_socials_explicitly(self):
        prompt = build_query_prompt("BA", "AR", 5, [])
        assert "instagram" in prompt
        assert "facebook" in prompt

    def test_handles_empty_domain_list(self):
        prompt = build_query_prompt("BA", "AR", 5, [])
        assert "starting from scratch" in prompt

    def test_specifies_spanish_query_english_intent(self):
        prompt = build_query_prompt("BA", "AR", 5, [])
        assert "Spanish" in prompt
        assert "English" in prompt


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _make_query(text: str, category: str = "teatro") -> DiscoveryQuery:
    return DiscoveryQuery(
        query=text, category=category, intent="test intent", target_neighborhood=None
    )


class TestValidateQueries:
    def test_passes_clean_list(self):
        payload = DiscoveryQueryList(queries=[_make_query(f"q{i}") for i in range(3)])
        result = validate_queries(payload, expected_count=3)
        assert result is payload

    def test_rejects_wrong_count(self):
        payload = DiscoveryQueryList(queries=[_make_query(f"q{i}") for i in range(2)])
        with pytest.raises(QueryValidationError, match="Expected 3"):
            validate_queries(payload, expected_count=3)

    def test_rejects_duplicate_query(self):
        payload = DiscoveryQueryList(queries=[_make_query("same"), _make_query("same")])
        with pytest.raises(QueryValidationError, match="Duplicate"):
            validate_queries(payload, expected_count=2)

    def test_dedupe_is_case_insensitive(self):
        payload = DiscoveryQueryList(
            queries=[_make_query("Teatro BA"), _make_query("teatro ba")]
        )
        with pytest.raises(QueryValidationError, match="Duplicate"):
            validate_queries(payload, expected_count=2)


# ---------------------------------------------------------------------------
# call_llm — mocked OpenRouter response
# ---------------------------------------------------------------------------


def _mock_response(content: str, prompt_tokens: int = 100, completion_tokens: int = 50):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )
    return response


def test_call_llm_parses_valid_json():
    valid_payload = json.dumps(
        {
            "queries": [
                {
                    "query": "teatro buenos aires cartelera",
                    "category": "teatro",
                    "intent": "Theatre listings",
                    "target_neighborhood": None,
                }
            ]
        }
    )
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_mock_response(valid_payload)
    )

    parsed, usage = asyncio.run(call_llm(client, "model-x", "prompt"))

    assert len(parsed.queries) == 1
    assert parsed.queries[0].query == "teatro buenos aires cartelera"
    assert usage == {"input_tokens": 100, "output_tokens": 50}


def test_call_llm_raises_on_non_json():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_mock_response("not json at all")
    )
    with pytest.raises(QueryValidationError, match="non-JSON"):
        asyncio.run(call_llm(client, "model-x", "prompt"))


def test_call_llm_raises_on_schema_mismatch():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_mock_response('{"unexpected": "shape"}')
    )
    with pytest.raises(QueryValidationError, match="Schema mismatch"):
        asyncio.run(call_llm(client, "model-x", "prompt"))


# ---------------------------------------------------------------------------
# write_output
# ---------------------------------------------------------------------------


def test_write_output_creates_json_file(tmp_path):
    queries = DiscoveryQueryList(queries=[_make_query("q1"), _make_query("q2")])
    config = DiscoveryConfig(city="Buenos Aires", n_queries=2, model="test/model")
    usage = {"input_tokens": 100, "output_tokens": 50}

    path = write_output(queries, ["foo.com"], config, usage, tmp_path)

    assert path.exists()
    assert path.parent == tmp_path
    payload = json.loads(path.read_text())
    assert payload["city"] == "Buenos Aires"
    assert payload["model"] == "test/model"
    assert payload["existing_domain_count"] == 1
    assert len(payload["queries"]) == 2
    assert "generated_at" in payload
    assert "estimated_cost_usd" in payload


def test_write_output_creates_missing_dir(tmp_path):
    queries = DiscoveryQueryList(queries=[_make_query("q1")])
    config = DiscoveryConfig(n_queries=1)
    target = tmp_path / "nested" / "out"

    path = write_output(queries, [], config, {}, target)

    assert path.exists()
    assert path.parent == target
