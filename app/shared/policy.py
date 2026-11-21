import tomllib
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from pathlib import Path

from app.shared.types import parse_hhmm

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "defaults.toml"
MIB = 1024 * 1024


@dataclass(frozen=True)
class DiscountChannel:
    app: str
    launch_url: str | None
    supports_paste: bool


@dataclass(frozen=True)
class Policy:
    tomorrow_start: time
    similar_max_diff: int
    event_radius_meters: int
    event_factor: Decimal
    event_categories: tuple[str, ...]
    weather_factor: dict[str, Decimal]
    weather_factor_by_category: dict[str, dict[str, Decimal]]
    menu_unit_default: str
    menu_unit_rules: tuple[tuple[str, str], ...]
    shelf_life_days: dict[str, int]
    short_shelf_life_max_days: int
    expensive_keywords: tuple[str, ...]
    recommended_min_menus: int
    order_days_min_receipt_dates: int
    price_change_alert_ratio: Decimal
    discount_base_time: time
    discount_recommended_percent: Decimal
    discount_start_minutes_before_close: int
    discount_surplus_within_days: int
    discount_channels: tuple[DiscountChannel, ...]
    sales_file_max_bytes: int
    sales_max_rows: int
    receipt_image_max_bytes: int
    receipt_images_total_max_bytes: int

    def weather_factor_for(self, category: str, condition: str | None) -> Decimal:
        if condition is None:
            return Decimal(1)
        by_category = self.weather_factor_by_category.get(category, {})
        return by_category.get(condition, self.weather_factor.get(condition, Decimal(1)))

    def uses_event_category(self, category: str | None) -> bool:
        return category is not None and category.startswith(self.event_categories)

    def menu_unit_for(self, menu_name: str) -> str:
        return next((unit for keyword, unit in self.menu_unit_rules if keyword in menu_name), self.menu_unit_default)

    def shelf_life_for(self, ingredient_name: str) -> int | None:
        if ingredient_name in self.shelf_life_days:
            return self.shelf_life_days[ingredient_name]
        contained = [key for key in self.shelf_life_days if key in ingredient_name]
        return self.shelf_life_days[max(contained, key=len)] if contained else None

    def tags_for(self, ingredient_name: str, shelf_life_days: int | None, used_in_menu_count: int) -> list[str]:
        short = shelf_life_days is not None and shelf_life_days <= self.short_shelf_life_max_days
        expensive = any(keyword in ingredient_name for keyword in self.expensive_keywords)
        tags = []
        if used_in_menu_count >= self.recommended_min_menus or short or expensive:
            tags.append("RECOMMENDED")
        if short:
            tags.append("SHORT_SHELF_LIFE")
        if expensive:
            tags.append("EXPENSIVE")
        return tags


def _decimals(table: dict) -> dict[str, Decimal]:
    return {key: Decimal(str(value)) for key, value in table.items()}


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> Policy:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    forecast, tags, discount, upload = raw["forecast"], raw["tags"], raw["discount"], raw["upload"]
    return Policy(
        tomorrow_start=parse_hhmm(raw["home"]["tomorrow_start"]),
        similar_max_diff=forecast["similar_max_diff"],
        event_radius_meters=forecast["event_radius_meters"],
        event_factor=Decimal(str(forecast["event_factor"])),
        event_categories=tuple(forecast["event_categories"]),
        weather_factor=_decimals(forecast["weather_factor"]),
        weather_factor_by_category={
            category: _decimals(table) for category, table in forecast.get("weather_factor_by_category", {}).items()
        },
        menu_unit_default=raw["menu_unit"]["default"],
        menu_unit_rules=tuple((rule["keyword"], rule["unit"]) for rule in raw["menu_unit"]["rules"]),
        shelf_life_days=dict(raw["shelf_life_days"]),
        short_shelf_life_max_days=tags["short_shelf_life_max_days"],
        expensive_keywords=tuple(tags["expensive_keywords"]),
        recommended_min_menus=tags["recommended_min_menus"],
        order_days_min_receipt_dates=raw["supplier"]["order_days_min_receipt_dates"],
        price_change_alert_ratio=Decimal(str(raw["price"]["change_alert_ratio"])),
        discount_base_time=parse_hhmm(discount["base_time"]),
        discount_recommended_percent=Decimal(str(discount["recommended_percent"])),
        discount_start_minutes_before_close=discount["start_minutes_before_close"],
        discount_surplus_within_days=discount["surplus_within_days"],
        discount_channels=tuple(
            DiscountChannel(channel["app"], channel["launch_url"] or None, channel["supports_paste"])
            for channel in discount["channels"]
        ),
        sales_file_max_bytes=upload["sales_file_max_mib"] * MIB,
        sales_max_rows=upload["sales_max_rows"],
        receipt_image_max_bytes=upload["receipt_image_max_mib"] * MIB,
        receipt_images_total_max_bytes=upload["receipt_images_total_max_mib"] * MIB,
    )
