"""payment idempotency and meta for ЮKassa

Revision ID: 0003_yookassa_prep
Revises: 0002_subscription
Create Date: 2026-05-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_yookassa_prep"
down_revision: str | None = "0002_subscription"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("payments", sa.Column("payment_meta", sa.JSON(), nullable=True))
    op.create_index("uq_payments_idempotency_key", "payments", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_payments_idempotency_key", table_name="payments")
    op.drop_column("payments", "payment_meta")
    op.drop_column("payments", "idempotency_key")
