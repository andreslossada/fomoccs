"""add source tier and per-source request throttle

Revision ID: d7e8f9g0h1i2
Revises: c5d6e7f8g9h0
Create Date: 2026-06-05 06:30:00.000000

Adds columns to support the source tiering and per-hostname throttling
introduced by the maximize-event-ingestion change.

sources:
  - tier: smallint 1-3 (1=official venue, 2=ticketing platform,
    3=stealth-required). Default 1 (lowest ban risk).
  - min_request_interval_seconds: numeric(4,2) override for the
    tier-default throttle. NULL = use the tier default at the
    crawler layer.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d7e8f9g0h1i2"
down_revision: str | Sequence[str] | None = "c5d6e7f8g9h0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "sources",
        sa.Column(
            "tier",
            sa.SmallInteger(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "min_request_interval_seconds",
            sa.Numeric(4, 2, asdecimal=False),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_sources_tier_range",
        "sources",
        "tier BETWEEN 1 AND 3",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_sources_tier_range", "sources", type_="check")
    op.drop_column("sources", "min_request_interval_seconds")
    op.drop_column("sources", "tier")
