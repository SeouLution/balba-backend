"""쿼리 작성용 테이블 정의. 제약·인덱스의 기준은 Alembic migration이다."""

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Float, Integer, MetaData, Numeric, Table, Text, Time
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
QTY = Numeric(18, 6)
TIMESTAMP = DateTime(timezone=True)

store = Table(
    "store",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text),
    Column("category", Text),
    Column("road_address", Text),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("business_hours", JSONB),
    Column("push_token", Text),
    Column("generation_status", Text),
    Column("first_forecast_at", TIMESTAMP),
    Column("forecast_model", JSONB),
    Column("created_at", TIMESTAMP),
)

menu = Table(
    "menu",
    metadata,
    Column("id", Text, primary_key=True),
    Column("store_id", Text),
    Column("name", Text),
)

ingredient = Table(
    "ingredient",
    metadata,
    Column("id", Text, primary_key=True),
    Column("store_id", Text),
    Column("name", Text),
    Column("unit", Text),
)

recipe_item = Table(
    "recipe_item",
    metadata,
    Column("menu_id", Text, primary_key=True),
    Column("ingredient_id", Text, primary_key=True),
    Column("quantity", QTY),
)

sales_import = Table(
    "sales_import",
    metadata,
    Column("id", Text, primary_key=True),
    Column("store_id", Text),
    Column("status", Text),
    Column("file_path", Text),
    Column("pos_system", Text),
    Column("detection", JSONB),
    Column("period_from", Date),
    Column("period_to", Date),
    Column("record_count", Integer),
    Column("created_at", TIMESTAMP),
)

daily_sales = Table(
    "daily_sales",
    metadata,
    Column("menu_id", Text, primary_key=True),
    Column("sold_on", Date, primary_key=True),
    Column("quantity", QTY),
)

supplier = Table(
    "supplier",
    metadata,
    Column("id", Text, primary_key=True),
    Column("store_id", Text),
    Column("name", Text),
    Column("phone", Text),
    Column("phone_read_on", Date),
    Column("phone_confirmed", Boolean),
    Column("order_days", JSONB),
    Column("order_days_status", Text),
)

receipt_scan = Table(
    "receipt_scan",
    metadata,
    Column("id", Text, primary_key=True),
    Column("store_id", Text),
    Column("status", Text),
    Column("images", JSONB),
    Column("result", JSONB),
    Column("created_at", TIMESTAMP),
)

receipt_line = Table(
    "receipt_line",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("supplier_id", Text),
    Column("purchased_on", Date),
    Column("ingredient_id", Text),
    Column("product_name", Text),
    Column("quantity", QTY),
    Column("unit_price", QTY),
)

forecast = Table(
    "forecast",
    metadata,
    Column("menu_id", Text, primary_key=True),
    Column("target_date", Date, primary_key=True),
    Column("recommended", QTY),
    Column("baseline", QTY),
    Column("event_diff", QTY),
    Column("summary_text", Text),
    Column("previous_recommended", QTY),
    Column("adjusted", QTY),
    Column("produced", QTY),
)

weather = Table(
    "weather",
    metadata,
    Column("store_id", Text, primary_key=True),
    Column("target_date", Date, primary_key=True),
    Column("temperature_c", Numeric(5, 1)),
    Column("condition", Text),
    Column("summary_text", Text),
    Column("change_text", Text),
)

weather_observation = Table(
    "weather_observation",
    metadata,
    Column("observed_on", Date, primary_key=True),
    Column("max_temperature_c", Numeric(5, 1)),
    Column("precipitation_mm", Numeric(7, 1)),
)

event = Table(
    "event",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text),
    Column("place", Text),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("start_at", TIMESTAMP),
    Column("end_at", TIMESTAMP),
    Column("crowd_level", Text),
    Column("expected_visitors", Integer),
    Column("peak_from", Time),
    Column("peak_to", Time),
    Column("detail_url", Text),
)

purchase_order = Table(
    "purchase_order",
    metadata,
    Column("id", Text, primary_key=True),
    Column("supplier_id", Text),
    Column("target_date", Date),
    Column("message_text", Text),
    Column("total_amount", BigInteger),
    Column("sent_channel", Text),
    Column("sent_at", TIMESTAMP),
    Column("created_at", TIMESTAMP),
)

purchase_order_line = Table(
    "purchase_order_line",
    metadata,
    Column("purchase_order_id", Text, primary_key=True),
    Column("ingredient_id", Text, primary_key=True),
    Column("quantity", QTY),
)

background_job = Table(
    "background_job",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text),
    Column("payload", JSONB),
    Column("dedupe_key", Text),
    Column("state", Text),
    Column("attempts", Integer),
    Column("available_at", TIMESTAMP),
    Column("last_error", Text),
    Column("created_at", TIMESTAMP),
    Column("finished_at", TIMESTAMP),
)

app_user = Table(
    "app_user",
    metadata,
    Column("id", Text, primary_key=True),
    Column("phone_number", Text),
    Column("password_hash", Text),
    Column("store_id", Text),
    Column("created_at", TIMESTAMP),
)
