"""수기 입고와 '현재 재고로 설정'을 영수증 입고와 같은 장부(receipt_line)에 저장

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE receipt_line ADD COLUMN store_id text REFERENCES store(id);
UPDATE receipt_line r SET store_id = s.store_id FROM supplier s WHERE s.id = r.supplier_id;
ALTER TABLE receipt_line ALTER COLUMN store_id SET NOT NULL;
ALTER TABLE receipt_line ADD COLUMN source text NOT NULL DEFAULT 'RECEIPT' CHECK (source IN ('RECEIPT','MANUAL'));
ALTER TABLE receipt_line ADD COLUMN is_stock_reset boolean NOT NULL DEFAULT false;
ALTER TABLE receipt_line ALTER COLUMN supplier_id DROP NOT NULL;
ALTER TABLE receipt_line ALTER COLUMN product_name DROP NOT NULL;
ALTER TABLE receipt_line DROP CONSTRAINT receipt_line_quantity_check;
ALTER TABLE receipt_line ADD CONSTRAINT receipt_line_quantity_check
  CHECK (quantity > 0 OR (is_stock_reset AND quantity >= 0));
ALTER TABLE receipt_line ADD CONSTRAINT receipt_line_receipt_has_supplier
  CHECK (source = 'MANUAL' OR (supplier_id IS NOT NULL AND product_name IS NOT NULL));
CREATE INDEX receipt_line_store_idx ON receipt_line (store_id, purchased_on, id)
"""

DOWNGRADE = """
DELETE FROM receipt_line WHERE source = 'MANUAL';
DROP INDEX receipt_line_store_idx;
ALTER TABLE receipt_line DROP CONSTRAINT receipt_line_receipt_has_supplier;
ALTER TABLE receipt_line DROP CONSTRAINT receipt_line_quantity_check;
ALTER TABLE receipt_line ADD CONSTRAINT receipt_line_quantity_check CHECK (quantity > 0);
ALTER TABLE receipt_line ALTER COLUMN product_name SET NOT NULL;
ALTER TABLE receipt_line ALTER COLUMN supplier_id SET NOT NULL;
ALTER TABLE receipt_line DROP COLUMN is_stock_reset;
ALTER TABLE receipt_line DROP COLUMN source;
ALTER TABLE receipt_line DROP COLUMN store_id
"""


def _run(script: str) -> None:
    for statement in script.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE)


def downgrade() -> None:
    _run(DOWNGRADE)
