# TUI Guide

The FomoCCS Admin TUI is a terminal-based admin panel built with
[Textual](https://textual.textualize.io/).

## Launch

```bash
fomoccs-tui
```

Requires the backend to be installed (`cd backend && uv sync`). The TUI
connects directly to the Supabase PostgreSQL database — no backend API
needed.

## Global shortcuts

| Key | Action |
|-----|--------|
| `q` | Quit |
| `arrows` | Navigate lists |
| `escape` | Go back / close |
| `tab` | Focus next widget |

## Screens

### Dashboard (`d`)
Overview of the system: DB health, active crawls, event stats, source summary,
LLM usage, recent events, last crawl jobs.

**Shortcuts:** `s` Sources, `e` Events, `l` Locations, `t` Tag Rules, `o` Operations, `g` Logs, `r` Refresh

### Sources (`s`)
Browse, filter, and manage event sources.

| Key | Action |
|-----|--------|
| `enter` | View source detail |
| `e` | Edit source |
| `d` | Delete source (soft delete) |
| `space` | Toggle active/disabled |
| `c` | Crawl this source now |
| `f` | Toggle force crawl flag |
| `a` | Filter: active only |
| `t` | Cycle tier filter (all → T1 → T2 → T3) |
| `n` | New source (opens wizard) |
| `r` | Refresh list |
| `m` | Load more |
| `/` | Focus search |

### Events (`e`)
Browse all events. Filter by search text and status (active/archived/pending).

| Key | Action |
|-----|--------|
| `enter` | View event detail |
| `a` | Toggle archive status |
| `c` | Cycle status filter |
| `r` | Refresh |
| `n` | Next page |
| `p` | Previous page |
| `/` | Focus search |

### Locations (`l`)
Browse venues. Filter by search and type.

| Key | Action |
|-----|--------|
| `enter` | View location detail |
| `c` | Cycle type filter |
| `r` | Refresh |
| `n` | Next page |
| `/` | Focus search |

### Tag Rules (`t`)
Manage tag transformation rules (rewrite, exclude, remove).

| Key | Action |
|-----|--------|
| `enter` | Edit rule |
| `n` | New rule |
| `d` | Delete rule |
| `r` | Refresh |
| `m` | Next page |

### Operations (`o`)
Trigger pipeline runs, view crawl job history, run maintenance scripts.

| Key | Action |
|-----|--------|
| `p` | Run full pipeline |
| `t` | Run pipeline for tier 1 only |
| `2` | Run pipeline for tier 2 only |
| `3` | Run pipeline for tier 3 only |
| `r` | Refresh crawl jobs list |

### Logs (`g`)
View structured pipeline logs from Cloud Logging (last 24h).

## Source Wizard

The wizard guides you through creating a new source:

1. Enter source name
2. Select type (primary/secondary)
3. Select crawl mode (browser/json_api)
4. Add URLs (one per line)
5. Select tier
6. Set crawl frequency (in days)
7. Optional: configure advanced settings

## Performance notes

- Dashboard auto-refreshes every 30 seconds
- Lists load 50 items per page
- Search has a 300ms debounce
- Detail screens load all relations eagerly (may be slow for sources with
  thousands of events)
