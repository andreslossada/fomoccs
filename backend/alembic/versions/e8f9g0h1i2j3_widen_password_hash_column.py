"""widen password_hash from VARCHAR(255) to TEXT

Revision ID: e8f9g0h1i2j3
Revises: d7e8f9g0h1i2
Create Date: 2026-06-16 00:00:00.000000

Prevents potential truncation of password hashes with future algorithms
or parameter upgrades that could produce hashes longer than 255 characters.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e8f9g0h1i2j3"
down_revision: str | Sequence[str] | None = "d7e8f9g0h1i2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.VARCHAR(255),
        type_=sa.TEXT(),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.TEXT(),
        type_=sa.VARCHAR(255),
        existing_nullable=False,
    )
