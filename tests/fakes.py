"""외부 포트의 fake. 테스트가 응답과 실패를 직접 정한다."""

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.infrastructure.providers.ports import (
    ColumnDetection,
    CrowdHour,
    CrowdPlace,
    DayWeather,
    EventRecord,
    GeoPoint,
    Providers,
    ReceiptReading,
    ReceiptReadingLine,
    RecipeDraft,
    RecipeDraftIngredient,
    RecipeDraftItem,
    RecipeDraftMenu,
)

JPEG_HEAD = b"\xff\xd8\xff\xe0"


def receipt_image(
    supplier: str | None, purchased_on: str | None, lines: list[dict], readable: bool = True, phone=None
) -> bytes:
    """FakeReceipts가 그대로 읽어 돌려주는 '사진'."""
    payload = {"readable": readable, "supplier": supplier, "purchasedOn": purchased_on, "lines": lines, "phone": phone}
    return JPEG_HEAD + json.dumps(payload, ensure_ascii=False).encode()


@dataclass
class Fake:
    error: Exception | None = None
    calls: int = 0

    def _enter(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


@dataclass
class FakeColumns(Fake):
    detection: ColumnDetection = ColumnDetection("주문일자", "상품명", "수량", "결제금액", "TOSS_POS")

    async def detect_columns(self, header, sample_rows):
        self._enter()
        return self.detection


@dataclass
class FakeReceipts(Fake):
    async def read_receipt(self, jpeg: bytes, ingredient_names):
        self._enter()
        payload = json.loads(jpeg[len(JPEG_HEAD) :])
        return ReceiptReading(
            readable=payload["readable"],
            supplier_name=payload["supplier"],
            phone=payload["phone"],
            purchased_on=date.fromisoformat(payload["purchasedOn"]) if payload["purchasedOn"] else None,
            lines=tuple(
                ReceiptReadingLine(
                    raw_product_name=line["name"],
                    quantity=Decimal(str(line["quantity"])) if line.get("quantity") is not None else None,
                    unit=line.get("unit"),
                    unit_price=line.get("unitPrice"),
                    content_quantity=Decimal(str(line["content"])) if line.get("content") else None,
                    usage_unit=line.get("usageUnit"),
                    match_name=line.get("match"),
                    match_kind=line.get("kind", "none"),
                )
                for line in payload["lines"]
            ),
        )


@dataclass
class FakeRecipes(Fake):
    """recipes: 메뉴 이름 → [(재료, 사용 단위, 사용량)]. purchase: 재료 → (구매 단위, 환산량)."""

    recipes: dict = field(default_factory=dict)
    purchase: dict = field(default_factory=dict)

    async def draft_recipes(self, category, menus, ingredient_names):
        self._enter()
        drafted = [
            RecipeDraftMenu(
                name,
                tuple(
                    RecipeDraftItem(ingredient, unit, Decimal(str(usage)))
                    for ingredient, unit, usage in self.recipes.get(name, [])
                ),
            )
            for name, _ in menus
        ]
        names = {item.ingredient_name for menu in drafted for item in menu.items}
        ingredients = []
        for name in sorted(names):
            purchase_unit, content = self.purchase.get(name, (None, None))
            ingredients.append(RecipeDraftIngredient(name, purchase_unit, Decimal(str(content)) if content else None))
        return RecipeDraft(tuple(drafted), tuple(ingredients))


@dataclass
class FakeGeocoder(Fake):
    point: GeoPoint | None = GeoPoint(37.5559, 126.9108)

    async def geocode(self, road_address):
        self._enter()
        return self.point


@dataclass
class FakeWeather(Fake):
    day: DayWeather | None = None

    async def day_weather(self, point, target_date, now):
        self._enter()
        return self.day or DayWeather((), None)


@dataclass
class FakeEvents(Fake):
    records: list[EventRecord] = field(default_factory=list)

    async def events_on(self, target_date):
        self._enter()
        return list(self.records)


@dataclass
class FakeCrowd(Fake):
    place_list: list[CrowdPlace] = field(default_factory=list)
    hours: list[CrowdHour] = field(default_factory=list)

    def places(self):
        return list(self.place_list)

    async def forecast(self, place):
        self._enter()
        return list(self.hours)


@dataclass
class FakePush(Fake):
    delivered: bool = True
    sent: list = field(default_factory=list)

    async def send(self, subscription_json, title, body, url):
        self._enter()
        self.sent.append((subscription_json, title, body, url))
        return self.delivered


def fake_providers() -> Providers:
    return Providers(
        columns=FakeColumns(),
        receipts=FakeReceipts(),
        recipes=FakeRecipes(),
        geocoder=FakeGeocoder(),
        weather=FakeWeather(),
        events=FakeEvents(),
        crowd=FakeCrowd(),
        push=FakePush(),
    )
