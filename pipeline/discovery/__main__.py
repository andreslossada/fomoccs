"""CLI entrypoint: python -m discovery [options]."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .query_generator import (
    DEFAULT_N_QUERIES,
    DiscoveryConfig,
    QueryValidationError,
    generate_queries,
    write_output,
)


def _print_summary(out_path: Path, queries, usage: dict, cost: float) -> None:
    print(f"\nWrote {len(queries.queries)} queries → {out_path}")
    print(
        f"Tokens: in={usage.get('input_tokens', 0)} "
        f"out={usage.get('output_tokens', 0)} | est. cost ${cost:.6f}\n"
    )
    print(f"{'#':<3} {'category':<14} {'neighborhood':<16} query")
    print("-" * 90)
    for i, q in enumerate(queries.queries, 1):
        nb = q.target_neighborhood or "-"
        print(f"{i:<3} {q.category:<14} {nb:<16} {q.query}")


async def run(args: argparse.Namespace) -> int:
    load_dotenv()
    api_url = os.environ.get("MOMAVERSE_API_URL")
    api_token = os.environ.get("MOMAVERSE_API_TOKEN")
    fetch_existing = bool(api_url and api_token) and not args.dry_run

    config = DiscoveryConfig(
        city=args.city,
        country=args.country,
        n_queries=args.n,
        api_url=api_url,
        api_token=api_token,
        fetch_existing=fetch_existing,
    )
    if args.model:
        config.model = args.model

    if not fetch_existing:
        print(
            "Note: skipping existing-domain dedupe "
            "(MOMAVERSE_API_URL/TOKEN unset or --dry-run).",
            file=sys.stderr,
        )

    try:
        queries, domains, usage = await generate_queries(config)
    except QueryValidationError as e:
        print(f"Validation failed: {e}", file=sys.stderr)
        return 2

    out_path = write_output(queries, domains, config, usage, Path(args.out_dir))
    from .query_generator import _cost_estimate

    cost = _cost_estimate(config.model, usage)
    _print_summary(out_path, queries, usage, cost)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="discovery",
        description="Generate Spanish search queries to discover new event sources.",
    )
    parser.add_argument("--city", default="Buenos Aires")
    parser.add_argument("--country", default="Argentina")
    parser.add_argument("--n", type=int, default=DEFAULT_N_QUERIES)
    parser.add_argument(
        "--model",
        default=None,
        help="Override DISCOVERY_QUERY_MODEL (e.g. openai/gpt-4o-mini).",
    )
    parser.add_argument(
        "--out-dir",
        default="out",
        help="Output directory for the JSON dump (default: ./out).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip fetching existing source domains.",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
