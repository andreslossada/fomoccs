"""add crawl_url_results table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-24 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "crawl_url_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("crawl_result_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending",
                "crawled",
                "extracted",
                "processed",
                "failed",
                name="crawl_result_status",
                create_type=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("crawled_content", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("crawled_at", sa.TIMESTAMP(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["crawl_result_id"], ["crawl_results.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("crawl_url_results")
