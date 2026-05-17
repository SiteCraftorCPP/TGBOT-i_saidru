"""user subscription_until

Revision ID: 0002_subscription
Revises: 0001_initial
Create Date: 2026-05-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_subscription"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("subscription_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "subscription_until")
