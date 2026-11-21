"""회원 계정

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
CREATE TABLE app_user (
  id text PRIMARY KEY,
  phone_number text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  store_id text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
)
"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE app_user")
