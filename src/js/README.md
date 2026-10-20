# JavaScript Modules

Vanilla JS, no framework. Modules are IIFEs that attach one global each and are
loaded as plain `<script>` tags in `index.html` — the build concatenates them in
document order, so **load order in `index.html` is a real dependency contract**,
not a convention.

Nothing is imported or exported. `build.js` reads the `<script src="js/...">`
list out of `index.html` and concatenates the files into a single minified
bundle, so adding a module means adding a tag there.

## Globals injected before any module

| Global | Source | Purpose |
|---|---|---|
| `window.__CITY__` | `config/<city>.yaml` via `build.js` | Map centre/zoom, timezone, filter roots, branding. Defensive default `{ map: {} }` keeps the app from hard-crashing when served unbuilt. |
| `var API_BASE_URL` | `API_BASE_URL` env var via `build.js` | Base URL for the live feed. Empty in local dev, the Cloud Run URL in production. |
| `flatpickr` | npm, bundled | Date range picker. |

## Load order

```
core      constants, utils, colorUtils, formatColors, iconCatalog,
          historyManager, urlParams
data      feedAdapter, filterManager, dataManager, searchManager
tags      tagColorManager, tagStateManager, sectionRenderer,
          searchController, filterPanelUI
ui        modalManager, toastNotifier, gestureHandler, sheet, iconManager,
          popupContentBuilder, listView, formatSelector, tagSelector,
          uiManager, themeManager, feedbackManager
map       dotDensityLayer, mapManager, viewportManager, markerController
          script.js  — the App orchestrator
```

## Core

| Module | Responsibility |
|---|---|
| `constants.js` | Frozen constants: durations, date maths, UI timings. |
| `utils.js` | Formatting and normalization helpers: `getTodayInZone`, `normalizeForSearch`, `parseOccurrences`, `getTagDisplayName`. Timezone comes from `__CITY__.timezone`, so "today" is the city's today, not the browser's. |
| `colorUtils.js` | OKLCH → hex, so theme colours derive from a hue rather than hand-picked hex values. |
| `formatColors.js` | Maps an event's format to an experience family (`Performance`, `Participatory`, `Outing`, …), which drives marker disc and dot-density colours. Family names must match the keys in the `hues` table here. |
| `iconCatalog.js` | Emoji-only icon catalog. Empty artwork, so `IconManager` rasterizes each record's own emoji. Replacing this file is how artwork packs get added back. |
| `historyManager.js` | Pushes filter state into the URL and restores it on back/forward. Tolerates legacy key names so old shared links keep working. |
| `urlParams.js` | Parses and writes the shareable URL query. |

## Data

| Module | Responsibility |
|---|---|
| `feedAdapter.js` | **The seam between this app's API and fomo.nyc's data shape.** Fetches `/api/v1/feed/events` and `/locations`, normalizes them (adds `event_type` from the tag taxonomy, splits curated tags from keywords), and drops records that cannot be placed. Everything downstream only sees the normalized shape. |
| `dataManager.js` | Owns the dataset: processing, indexing, tag hierarchy maps, search index. Also `fetchData` for the static JSON under `data/`. |
| `filterManager.js` | Pure filter predicates — date range, tags, location grouping. No DOM. |
| `searchManager.js` | Scoring and ranking for omni-search across events, venues and tags. |

## Tags

| Module | Responsibility |
|---|---|
| `tagStateManager.js` | Single source of truth for tag selection: unselected / selected / required / forbidden. |
| `tagColorManager.js` | Assigns a colour per tag from the theme palette, and extracts a dominant colour from rendered emoji pixels. |
| `sectionRenderer.js` | Renders the grouped result list in `#results-container`. |
| `searchController.js` | Binds the search box to the renderer. |
| `filterPanelUI.js` | Wires the selectors and search together and exposes the tag state the rest of the app reads. |

## UI

| Module | Responsibility |
|---|---|
| `sheet.js` | The event list sheet: a left slide-in on desktop, a bottom sheet on mobile. Replaces the older `bottomSheet.js`. |
| `listView.js` | Builds the grouped event rows inside the sheet. |
| `tagSelector.js` | Hierarchical tag popover (`createTagSelector`, instantiated once as `TagSelector`). |
| `formatSelector.js` | Format popover, grouped by experience family. Independent OR filter intersected with dates and topics. |
| `popupContentBuilder.js` | Builds the marker popup DOM. |
| `iconManager.js` | Rasterizes icons to canvas: emoji glyphs now, artwork if a catalog is restored. Returns a data URL, so consumers are unaware of which path ran. |
| `uiManager.js` | Owns the date picker, the logo menu and the panel chrome. |
| `modalManager.js` | Welcome, feedback and settings dialogs. |
| `themeManager.js` | Dark/light switching and the map-label webfont. |
| `toastNotifier.js` | Transient notifications. |
| `gestureHandler.js` | Desktop section reorder; disabled on mobile where it fights the tag scroller. |
| `feedbackManager.js` | Posts to `/api/v1/feedback/`. |

## Map

| Module | Responsibility |
|---|---|
| `mapManager.js` | MapLibre instance, sources, layers, marker promotion. |
| `viewportManager.js` | Debounced viewport queries and "is this point on screen". |
| `markerController.js` | Click → popup, popup lifecycle, label cache. |
| `dotDensityLayer.js` | Density dots for low zoom levels, where individual markers would overlap illegibly. |

## Entry point

`script.js` defines `App` inside a `DOMContentLoaded` closure and is the only
module that touches application state directly. In dev builds `build.js`
injects `window.__fomo = App` after `App.init()`; production does not expose it.

## Data flow

```
config/<city>.yaml ──build.js──▶ window.__CITY__ ─┐
data/tags.json ────────────────┐                  │
data/tag_hierarchy.json ───────┼──▶ _loadInitialData ──▶ FeedAdapter.load()
                                │                         │
/api/v1/feed/events   ─────────┤                         ▼
/api/v1/feed/locations ────────┴────────────────▶ normalized events
                                                          │
                          processInitialData ─▶ tag hierarchy ─▶ indexes
                                                          │
                                            filterAndDisplayEvents
                                                          │
                                          map layers + sheet + selectors
```

The feed is fetched **once**, not in per-day chunks: the API already returns the
whole window in one response, so there is no two-phase load to coordinate.