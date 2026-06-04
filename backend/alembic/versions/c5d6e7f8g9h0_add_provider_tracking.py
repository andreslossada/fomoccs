"""add llm provider tracking columns

Revision ID: c5d6e7f8g9h0
Revises: b2c3d4e5f6a7
Create Date: 2026-06-03 00:00:00.000000

Adds columns to track which LLM provider was used for extraction, enabling
visibility into fallback behavior (Gemini -> Gemini-Lite -> OpenRouter -> xAI).

crawl_results:
  - extraction_provider: label of the provider that actually served the call
  - extraction_model: model name on that provider
  - extraction_attempts: total API attempts (1 if primary worked, >1 with fallbacks)
  - extraction_fallbacks: how many times we fell through to a different provider

crawl_summaries:
  - providers_used: JSONB map {provider_label: call_count}
  - rate_limited_count: how many calls hit 429 across all providers
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8g9h0"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "crawl_results",
        sa.Column("extraction_provider", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "crawl_results",
        sa.Column("extraction_model", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "crawl_results",
        sa.Column("extraction_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "crawl_results",
        sa.Column(
            "extraction_fallbacks", sa.Integer(), server_default="0", nullable=False
        ),
    )

    op.add_column(
        "crawl_summaries",
        sa.Column("providers_used", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "crawl_summaries",
        sa.Column(
            "rate_limited_count", sa.Integer(), server_default="0", nullable=False
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("crawl_summaries", "rate_limited_count")
    op.drop_column("crawl_summaries", "providers_used")

    op.drop_column("crawl_results", "extraction_fallbacks")
    op.drop_column("crawl_results", "extraction_attempts")
    op.drop_column("crawl_results", "extraction_model")
    op.drop_column("crawl_results", "extraction_provider")
