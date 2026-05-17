"""payment_kind and nullable document_id for subscription payments

Revision ID: 0004_payment_kind
Revises: 0003_yookassa_prep
Create Date: 2026-05-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_payment_kind"
down_revision: str | None = "0003_yookassa_prep"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        with op.batch_alter_table("payments") as batch:
            batch.alter_column("document_id", existing_type=sa.Integer(), nullable=True)
    else:
        op.alter_column("payments", "document_id", existing_type=sa.Integer(), nullable=True)

    op.add_column(
        "payments",
        sa.Column(
            "payment_kind",
            sa.String(length=32),
            nullable=False,
            server_default="document",
        ),
    )


def downgrade() -> None:
    op.drop_column("payments", "payment_kind")
    op.execute(sa.text("DELETE FROM payments WHERE document_id IS NULL"))
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        with op.batch_alter_table("payments") as batch:
            batch.alter_column("document_id", existing_type=sa.Integer(), nullable=False)
    else:
        op.alter_column("payments", "document_id", existing_type=sa.Integer(), nullable=False)
