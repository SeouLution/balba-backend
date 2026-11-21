"""할인·핵심 재료·재료 배지 삭제, 재료 단위 개·g 통일, 예측 모델과 과거 날씨 관측

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

JOB_KINDS = (
    "'GEOCODE_STORE','PARSE_SALES','APPLY_SALES','GENERATE_RECIPES',"
    "'SCAN_RECEIPTS','REFRESH_CONTEXT','GENERATE_FORECAST','SEND_PUSH'"
)

GUARD = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM ingredient i
    WHERE (i.usage_unit IS NULL OR i.content_quantity IS NULL)
      AND (EXISTS (SELECT 1 FROM receipt_line r WHERE r.ingredient_id = i.id)
        OR EXISTS (SELECT 1 FROM purchase_order_line p WHERE p.ingredient_id = i.id))
  ) THEN
    RAISE EXCEPTION '개·g으로 옮길 수 없습니다: 사용 단위나 환산량이 없는 재료에 입고·발주 기록이 있습니다';
  END IF;
END $$
"""

UPGRADE = """
UPDATE recipe_item r SET quantity = r.quantity * 1000
  FROM ingredient i WHERE i.id = r.ingredient_id AND lower(i.usage_unit) IN ('kg', 'l');
UPDATE ingredient SET content_quantity = content_quantity * 1000 WHERE lower(usage_unit) IN ('kg', 'l');

ALTER TABLE receipt_line ALTER COLUMN unit_price TYPE numeric(18,6);
UPDATE receipt_line r SET quantity = r.quantity * i.content_quantity, unit_price = r.unit_price / i.content_quantity
  FROM ingredient i WHERE i.id = r.ingredient_id;
UPDATE purchase_order_line p SET quantity = p.quantity * i.content_quantity
  FROM ingredient i WHERE i.id = p.ingredient_id;

ALTER TABLE ingredient ADD COLUMN unit text;
UPDATE ingredient SET unit = CASE WHEN lower(usage_unit) IN ('g', 'kg', 'ml', 'l') THEN 'g' ELSE '개' END;
ALTER TABLE ingredient ALTER COLUMN unit SET NOT NULL;
ALTER TABLE ingredient ADD CONSTRAINT ingredient_unit_check CHECK (unit IN ('개', 'g'));
ALTER TABLE ingredient DROP COLUMN usage_unit, DROP COLUMN purchase_unit, DROP COLUMN content_quantity,
  DROP COLUMN status, DROP COLUMN tags;

ALTER TABLE menu DROP COLUMN unit, DROP COLUMN price;
DROP TABLE discount;

ALTER TABLE store ADD COLUMN forecast_model jsonb;
CREATE TABLE weather_observation (
  observed_on date PRIMARY KEY,
  max_temperature_c numeric(5,1) NOT NULL,
  precipitation_mm numeric(7,1) NOT NULL CHECK (precipitation_mm >= 0)
);

ALTER TABLE background_job DROP CONSTRAINT background_job_kind_check;
ALTER TABLE background_job ADD CONSTRAINT background_job_kind_check
  CHECK (kind IN ({kinds},'FIT_FORECAST_MODEL'))
""".format(kinds=JOB_KINDS)

DOWNGRADE = """
DELETE FROM background_job WHERE kind = 'FIT_FORECAST_MODEL';
ALTER TABLE background_job DROP CONSTRAINT background_job_kind_check;
ALTER TABLE background_job ADD CONSTRAINT background_job_kind_check CHECK (kind IN ({kinds}));

DROP TABLE weather_observation;
ALTER TABLE store DROP COLUMN forecast_model;

CREATE TABLE discount (
  id text PRIMARY KEY,
  menu_id text NOT NULL REFERENCES menu(id),
  discount_type text NOT NULL CHECK (discount_type IN ('PERCENT','AMOUNT')),
  discount_value numeric(18,6) NOT NULL CHECK (discount_value > 0),
  start_time time NOT NULL,
  quantity numeric(18,6) NOT NULL CHECK (quantity >= 1),
  original_price bigint NOT NULL CHECK (original_price >= 0),
  waste_cost_avoided bigint CHECK (waste_cost_avoided >= 0),
  revenue_reduction bigint CHECK (revenue_reduction >= 0),
  promo_text text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX discount_menu_idx ON discount (menu_id, created_at DESC);
ALTER TABLE menu ADD COLUMN unit text NOT NULL DEFAULT '인분', ADD COLUMN price bigint CHECK (price >= 0);
ALTER TABLE menu ALTER COLUMN unit DROP DEFAULT;

ALTER TABLE ingredient ADD COLUMN usage_unit text, ADD COLUMN purchase_unit text,
  ADD COLUMN content_quantity numeric(18,6) CHECK (content_quantity > 0),
  ADD COLUMN status text NOT NULL DEFAULT 'LATER' CHECK (status IN ('CORE','LATER')),
  ADD COLUMN tags jsonb NOT NULL DEFAULT '[]';
UPDATE ingredient SET usage_unit = unit, purchase_unit = unit, content_quantity = 1, status = 'CORE';
ALTER TABLE ingredient DROP COLUMN unit;
ALTER TABLE receipt_line ALTER COLUMN unit_price TYPE bigint USING round(unit_price)
""".format(kinds=JOB_KINDS)


def _run(script: str) -> None:
    for statement in script.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    op.execute(GUARD)
    _run(UPGRADE)


def downgrade() -> None:
    _run(DOWNGRADE)
