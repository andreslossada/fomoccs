# CSS files

## File organization

```
css/
├── index.css               # Entry point — @imports everything below
├── variables.css           # Design tokens: colour, radii, shadows, spacing
├── fonts.css               # Self-hosted Inter + the font stack
├── layout.css              # Page shell, map container, app chrome
├── filter-panel.css        # Left sidebar: search, date picker, selector row
├── tags.css                # Tag chips and their states
├── sheet.css               # Event list sheet (left slide-in / bottom sheet)
├── map.css                 # MapLibre container, markers, controls
├── popups.css              # Marker popups and event cards
├── modals.css              # Dialogs, settings, toasts
├── interests.css           # Favourite chips
└── structured-search.css   # Structured search dialog
```

`build.js` bundles the whole chain with esbuild (which resolves `@import`
natively) and emits one hashed `app.<hash>.css`. `index.html` references only
the bundle, never the individual files.

Two import groups exist for a reason: the first three files are foundation
(tokens → fonts → layout) and every component file after them can be reordered
or dropped without a cascade breaking.

## Foundation

### `variables.css`
The whole design system as custom properties on `:root`, with light-theme
overrides under `[data-theme="light"]`. Components never hard-code a colour;
they read a token. Anything shared between components lives here — the radius
scale (`--radius-pill`, `--radius-card`, `--radius-round`, …) in particular, so
a change of corner radius is one edit rather than a search across the codebase.

### `fonts.css`
Declares the self-hosted Inter variable font from `../fonts/inter/`. Self-
hosting removes a third-party origin (DNS plus TLS handshake) from the critical
path; the build rewrites `../fonts/` to `fonts/` because the bundle sits at the
root of `dist/` rather than under `css/`.

### `layout.css`
The page shell: `#app-container`, the map container, and the stacking context
that lets the filter panel overlay the map on desktop and share a row with it
on mobile.

## Components

Each component file is self-contained and carries its own responsive rules, so a
component can be read, changed or removed without reading the others.

- **`filter-panel.css`** — the sidebar, search input, flatpickr integration, and
  the `.selector-controls` row that holds the format and tag popover triggers.
- **`sheet.css`** — one component, two presentations: a left slide-in on
  desktop (with the floating `#sheet-handle` reopen affordance) and a bottom
  sheet with a drag grip on mobile.
- **`map.css`** — MapLibre container, marker layers, zoom/attribution controls.
- **`popups.css`** — marker popup chrome and the event cards inside the sheet.
- **`modals.css`** — dialog, settings, toast.
- **`tags.css`** — tag chip appearance and selected/excluded states.
- **`interests.css` / `structured-search.css`** — styles for the favourites
  chips and the structured-search dialog.

## Conventions

- Tokens over literals. A raw hex in a component file is a bug unless it is
  artwork.
- Both themes must look deliberate. A component that only works on the default
  dark theme is unfinished.
- Mobile rules live in the component that owns them, not in one global media
  block, so a component can be deleted in one step.