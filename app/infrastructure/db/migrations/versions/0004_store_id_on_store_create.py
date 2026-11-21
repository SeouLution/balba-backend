"""가게 id는 가게 정보를 저장할 때 발급

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app_user ALTER COLUMN store_id DROP NOT NULL")
    op.execute("UPDATE app_user SET store_id = NULL WHERE store_id NOT IN (SELECT id FROM store)")


def downgrade() -> None:
    op.execute("UPDATE app_user SET store_id = 'st_' || md5(id) WHERE store_id IS NULL")
    op.execute("ALTER TABLE app_user ALTER COLUMN store_id SET NOT NULL")
