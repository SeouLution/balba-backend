import tomllib
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from pathlib import Path

from app.shared.types import parse_hhmm

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "defaults.toml"
MIB = 1024 * 1024


@dataclass(frozen=True)
class Policy:
    tomorrow_start: time
    similar_max_diff: int
    event_radius_meters: int
    event_factor: Decimal
    event_categories: tuple[str, ...]
    min_weekday_days: int
    min_bucket_days: int
    temperature_bands: tuple[Decimal, ...]
    wet_min_mm: Decimal
    index_min: Decimal
    index_max: Decimal
    fit_min_days: int
    observation_station: str
    order_days_min_receipt_dates: int
    low_stock_days: int
    sales_file_max_bytes: int
    sales_max_rows: int
    receipt_image_max_bytes: int
    receipt_images_total_max_bytes: int

    def uses_event_category(self, category: str | None) -> bool:
        return category is not None and category.startswith(self.event_categories)


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> Policy:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    forecast, upload = raw["forecast"], raw["upload"]
    return Policy(
        tomorrow_start=parse_hhmm(raw["home"]["tomorrow_start"]),
        similar_max_diff=forecast["similar_max_diff"],
        event_radius_meters=forecast["event_radius_meters"],
        event_factor=_decimal(forecast["event_factor"]),
        event_categories=tuple(forecast["event_categories"]),
        min_weekday_days=forecast["min_weekday_days"],
        min_bucket_days=forecast["min_bucket_days"],
        temperature_bands=tuple(_decimal(bound) for bound in forecast["temperature_bands"]),
        wet_min_mm=_decimal(forecast["wet_min_mm"]),
        index_min=_decimal(forecast["index_min"]),
        index_max=_decimal(forecast["index_max"]),
        fit_min_days=forecast["fit_min_days"],
        observation_station=forecast["observation_station"],
        order_days_min_receipt_dates=raw["supplier"]["order_days_min_receipt_dates"],
        low_stock_days=raw["low_stock"]["days"],
        sales_file_max_bytes=upload["sales_file_max_mib"] * MIB,
        sales_max_rows=upload["sales_max_rows"],
        receipt_image_max_bytes=upload["receipt_image_max_mib"] * MIB,
        receipt_images_total_max_bytes=upload["receipt_images_total_max_mib"] * MIB,
    )
