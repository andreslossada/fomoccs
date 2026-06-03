"""Tests for crawler.py JSON API mapping and JSONP handling."""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler import (
    _pick_emoji,
    map_json_api_to_extracted,
    strip_jsonp,
)

# =============================================================================
# strip_jsonp tests
# =============================================================================


class TestStripJsonp:
    """Tests for JSONP callback stripping."""

    def test_plain_json_unchanged(self):
        text = '{"key": "value"}'
        assert strip_jsonp(text) == text

    def test_strips_named_callback(self):
        text = 'myCallback({"key": "value"});'
        assert strip_jsonp(text, "myCallback") == '{"key": "value"}'

    def test_strips_generic_callback(self):
        text = 'someFunc({"key": "value"});'
        result = strip_jsonp(text)
        assert result == '{"key": "value"}'

    def test_strips_callback_without_semicolon(self):
        text = 'cb({"key": "value"})'
        result = strip_jsonp(text)
        assert result == '{"key": "value"}'

    def test_handles_whitespace(self):
        text = '  myCallback({"key": "value"}) ; '
        result = strip_jsonp(text, "myCallback")
        assert result == '{"key": "value"}'

    def test_plain_json_with_bom(self):
        """BOM is stripped upstream, but plain JSON should pass through."""
        text = '{"key": "value"}'
        assert strip_jsonp(text) == text


# =============================================================================
# _pick_emoji tests
# =============================================================================


class TestPickEmoji:
    """Tests for emoji selection from clasificaciones."""

    def test_teatro_maps_to_theater(self):
        clasificaciones = {"1": {"descripcion": "Teatro"}}
        assert _pick_emoji(clasificaciones) == "\U0001f3ad"

    def test_danza_maps_to_dancer(self):
        clasificaciones = {"1": {"descripcion": "Danza"}}
        assert _pick_emoji(clasificaciones) == "\U0001f483"

    def test_humor_maps_to_laugh(self):
        clasificaciones = {"1": {"descripcion": "Humor"}}
        assert _pick_emoji(clasificaciones) == "\U0001f923"

    def test_empty_dict_returns_default(self):
        assert _pick_emoji({}) == "\U0001f3ad"

    def test_non_dict_returns_calendar(self):
        assert _pick_emoji(None) == "\U0001f4c5"
        assert _pick_emoji("not a dict") == "\U0001f4c5"

    def test_unknown_clasificacion_returns_performing_arts(self):
        clasificaciones = {"1": {"descripcion": "UnknownGenre"}}
        assert _pick_emoji(clasificaciones) == "\U0001f3ad"


# =============================================================================
# map_json_api_to_extracted tests
# =============================================================================


def _make_event(
    titulo="Test Show",
    url_slug="obra12345-test-show",
    clasificaciones=None,
    lugares=None,
):
    """Helper to build an Alternativa Teatral event dict."""
    if clasificaciones is None:
        clasificaciones = {"1": {"descripcion": "Teatro"}}
    if lugares is None:
        lugares = {
            "100": {
                "nombre": "Teatro Teresa Carreño",
                "direccion": "Av. Urdaneta 1530",
                "zona": "Caracas",
                "funciones": {
                    "200": {
                        "dia": "Viernes",
                        "hora": "20:00",
                        "proxima_fecha": "2026-04-10 20:00",
                    }
                },
            }
        }
    return {
        "titulo": titulo,
        "url": url_slug,
        "clasificaciones": clasificaciones,
        "lugares": lugares,
    }


class TestMapJsonApiToExtracted:
    """Tests for the Alternativa Teatral direct mapping."""

    def test_basic_mapping_produces_valid_dict(self):
        events_dict = {"1": _make_event()}
        data = map_json_api_to_extracted(events_dict)
        assert "events" in data
        assert len(data["events"]) == 1

    def test_event_fields_mapped_correctly(self):
        events_dict = {"1": _make_event()}
        data = map_json_api_to_extracted(events_dict)
        event = data["events"][0]

        assert event["name"] == "Test Show"
        assert event["location"] == "Teatro San Martin"
        assert event["url"] == "https://www.alternativateatral.com/obra12345-test-show"
        assert event["sublocation"] is None
        assert event["hashtags"] == ["Teatro"]
        assert event["emoji"] == "\U0001f3ad"

    def test_occurrences_parsed_from_proxima_fecha(self):
        events_dict = {"1": _make_event()}
        data = map_json_api_to_extracted(events_dict)
        occ = data["events"][0]["occurrences"][0]

        assert occ["start_date"] == "2026-04-10"
        assert occ["start_time"] == "8:00 PM"
        assert occ["end_date"] is None
        assert occ["end_time"] is None

    def test_multiple_funciones_create_multiple_occurrences(self):
        lugares = {
            "100": {
                "nombre": "Teatro X",
                "funciones": {
                    "1": {
                        "dia": "Lunes",
                        "hora": "20:00",
                        "proxima_fecha": "2026-04-10 20:00",
                    },
                    "2": {
                        "dia": "Martes",
                        "hora": "21:00",
                        "proxima_fecha": "2026-04-11 21:00",
                    },
                },
            }
        }
        events_dict = {"1": _make_event(lugares=lugares)}
        data = map_json_api_to_extracted(events_dict)
        assert len(data["events"][0]["occurrences"]) == 2

    def test_multiple_lugares_create_separate_events(self):
        """Each venue produces a separate event entry."""
        lugares = {
            "100": {
                "nombre": "Teatro A",
                "funciones": {"1": {"proxima_fecha": "2026-04-10 20:00"}},
            },
            "200": {
                "nombre": "Teatro B",
                "funciones": {"2": {"proxima_fecha": "2026-04-11 21:00"}},
            },
        }
        events_dict = {"1": _make_event(lugares=lugares)}
        data = map_json_api_to_extracted(events_dict)
        assert len(data["events"]) == 2
        locations = {e["location"] for e in data["events"]}
        assert locations == {"Teatro A", "Teatro B"}

    def test_empty_events_dict(self):
        data = map_json_api_to_extracted({})
        assert data == {"events": []}

    def test_event_without_funciones_skipped(self):
        lugares = {"100": {"nombre": "Teatro X", "funciones": {}}}
        events_dict = {"1": _make_event(lugares=lugares)}
        data = map_json_api_to_extracted(events_dict)
        assert len(data["events"]) == 0

    def test_funcion_without_proxima_fecha_skipped(self):
        """Funciones with only hora but no proxima_fecha are skipped."""
        lugares = {
            "100": {
                "nombre": "Teatro X",
                "funciones": {"1": {"dia": "Viernes", "hora": "20:00"}},
            }
        }
        events_dict = {"1": _make_event(lugares=lugares)}
        data = map_json_api_to_extracted(events_dict)
        assert len(data["events"]) == 0

    def test_lugar_without_nombre_skipped(self):
        lugares = {
            "100": {
                "nombre": "",
                "funciones": {"1": {"proxima_fecha": "2026-04-10 20:00"}},
            }
        }
        events_dict = {"1": _make_event(lugares=lugares)}
        data = map_json_api_to_extracted(events_dict)
        assert len(data["events"]) == 0

    def test_description_includes_clasificaciones_and_venue(self):
        clasificaciones = {
            "1": {"descripcion": "Teatro"},
            "2": {"descripcion": "Humor"},
        }
        events_dict = {"1": _make_event(clasificaciones=clasificaciones)}
        data = map_json_api_to_extracted(events_dict)
        desc = data["events"][0]["description"]
        assert "Teatro" in desc
        assert "Humor" in desc
        assert "Teatro San Martin" in desc

    def test_hashtags_from_clasificaciones(self):
        clasificaciones = {
            "1": {"descripcion": "Teatro"},
            "2": {"descripcion": "Humor"},
        }
        events_dict = {"1": _make_event(clasificaciones=clasificaciones)}
        data = map_json_api_to_extracted(events_dict)
        assert data["events"][0]["hashtags"] == ["Teatro", "Humor"]

    def test_no_clasificaciones_defaults_to_teatro(self):
        events_dict = {"1": _make_event(clasificaciones={})}
        data = map_json_api_to_extracted(events_dict)
        assert data["events"][0]["hashtags"] == ["Teatro"]

    def test_url_none_when_no_slug(self):
        events_dict = {"1": _make_event(url_slug="")}
        data = map_json_api_to_extracted(events_dict)
        assert data["events"][0]["url"] is None

    def test_output_compatible_with_parse_json_events(self):
        """Verify the output format is parseable by _parse_json_events."""
        events_dict = {"1": _make_event()}
        data = map_json_api_to_extracted(events_dict)

        # Validate structure matches what _parse_json_events expects
        assert "events" in data
        for event in data["events"]:
            assert "name" in event
            assert "location" in event
            assert "occurrences" in event
            assert isinstance(event["occurrences"], list)
            for occ in event["occurrences"]:
                assert "start_date" in occ
            assert "description" in event
            assert "hashtags" in event
            assert "emoji" in event

    def test_proxima_fecha_time_conversion(self):
        """Various time formats from proxima_fecha."""
        lugares = {
            "100": {
                "nombre": "Teatro X",
                "funciones": {
                    "1": {"proxima_fecha": "2026-04-10 09:30"},
                    "2": {"proxima_fecha": "2026-04-10 14:00"},
                    "3": {"proxima_fecha": "2026-04-10 00:00"},
                },
            }
        }
        events_dict = {"1": _make_event(lugares=lugares)}
        data = map_json_api_to_extracted(events_dict)
        times = [o["start_time"] for o in data["events"][0]["occurrences"]]
        assert "9:30 AM" in times
        assert "2:00 PM" in times
        assert "12:00 AM" in times
