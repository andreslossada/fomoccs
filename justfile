# FomoCCS — command recipes
# Run with: just <recipe>
# Install: cargo install just  (or choco install just / brew install just)

# ── Backend ──────────────────────────────────────────────────────────

# Start the FastAPI dev server
api:
    cd backend && uv run uvicorn api.main:app --reload

# Launch the admin TUI
tui:
    cd backend && uv run fomoccs-tui

# Run backend tests (needs Docker for testcontainers)
test-backend:
    cd backend && uv run pytest tests/ -x -q

# Run backend lint + format
lint-backend:
    cd backend && uv run ruff check . && uv run ruff format --check .

# ── Pipeline ────────────────────────────────────────────────────────

# Run pipeline for all due sources
pipeline:
    cd pipeline && python main.py

# Run pipeline for specific source IDs
pipeline-id ids:
    cd pipeline && python main.py --ids {{ids}}

# Run pipeline for a specific tier
pipeline-tier tier:
    cd pipeline && python main.py --tier {{tier}}

# Run pipeline tests
test-pipeline:
    cd pipeline && uv run pytest tests/ -x -q

# ── Frontend ────────────────────────────────────────────────────────

# Build frontend to dist/
build-frontend:
    npm run build

# Start frontend dev server
dev-frontend:
    npm run dev

# ── All ─────────────────────────────────────────────────────────────

# Run all tests
test-all: test-backend test-pipeline

# Run all lints
lint-all: lint-backend
    cd pipeline && uv run ruff check .

# ── Instagram ────────────────────────────────────────────────────────

# Harvest Instagram profile posts
ig-harvest username max="20":
    python scripts/instagram_harvest.py --username {{username}} --max-posts {{max}} --pretty

# Harvest single Instagram post
ig-post url:
    python scripts/instagram_harvest.py --post "{{url}}" --pretty

# ── DB ───────────────────────────────────────────────────────────────

# Run DB migrations (uses supabase CLI)
db-migrate:
    supabase db push
