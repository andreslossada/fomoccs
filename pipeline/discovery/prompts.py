"""Prompt assembly for the source-discovery query generator."""

QUERY_GENERATOR_SCHEMA_HINT = """\
Return ONLY valid JSON in this exact shape:
{
  "queries": [
    {
      "query": "<Spanish search query string>",
      "category": "<one of: teatro, música, arte, cine, danza, ferias, museos, gastronomía, niños, gratis, otros>",
      "intent": "<one short English sentence explaining what listings this query is meant to surface>",
      "target_neighborhood": "<lowercase neighborhood name or null>"
    }
  ]
}
"""


def build_query_prompt(
    city: str,
    country: str,
    n_queries: int,
    existing_domains: list[str],
) -> str:
    """Assemble the query-generator prompt.

    Instruction is in English; generated queries must be in Spanish.
    Diversity rules are baked in to push for broad coverage.
    """
    if existing_domains:
        excluded = "\n".join(f"  - {d}" for d in sorted(set(existing_domains)))
    else:
        excluded = "  (none — discovery is starting from scratch)"

    return f"""You are helping bootstrap a database of event sources for a city events
aggregator. Your job: produce exactly {n_queries} Spanish-language web-search
queries that, when typed into Google, surface websites publishing UPCOMING
event listings (calendars, agendas, carteleras) in {city}, {country}.

DIVERSITY RULES — every query must distinctly cover at least one of:
  - a different event category (teatro, música, arte, cine, danza, ferias,
    museos, gastronomía, niños, gratis)
  - a different neighborhood of {city} (e.g. Palermo, San Telmo, Recoleta,
    Villa Crespo, Chacarita, Colegiales, Belgrano, Balvanera, Caballito)
  - a different agenda keyword (cartelera, agenda, programación,
    qué hacer, calendario)

Hard constraints:
  - No two queries may share more than 2 content words.
  - At least 3 queries must be long-tail (5 or more words).
  - Mix city-wide and neighborhood-scoped queries.
  - Never target social-media domains (instagram, facebook, tiktok, twitter, x).
  - Avoid phrasings that surface news articles about a single event; favor
    listing/calendar pages with multiple upcoming events.
  - Do not produce queries whose obvious top hit is one of these existing
    source domains:
{excluded}

Output language:
  - The "query" field MUST be in Spanish (the language locals use to search).
  - The "intent" field MUST be in English (for the human reviewer).

{QUERY_GENERATOR_SCHEMA_HINT}
"""
