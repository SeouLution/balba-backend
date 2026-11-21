"""외부 서비스 포트와 그 경계에서 쓰는 타입. 외부 응답 JSON은 adapter 밖으로 나가지 않는다."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol


class ProviderUnavailable(Exception):
    """timeout, 429, 5xx처럼 다시 시도하면 될 수 있는 실패."""


class ProviderInvalidPayload(Exception):
    """응답은 받았지만 계약에 맞지 않아 쓸 수 없는 경우."""


@dataclass(frozen=True)
class ColumnDetection:
    date: str
    menu_name: str
    quantity: str
    pos_system: str


@dataclass(frozen=True)
class ReceiptReadingLine:
    raw_product_name: str
    amount: Decimal | None
    unit: str | None
    line_price: int | None
    match_name: str | None
    match_kind: str


@dataclass(frozen=True)
class ReceiptReading:
    readable: bool
    supplier_name: str | None = None
    phone: str | None = None
    purchased_on: date | None = None
    lines: tuple[ReceiptReadingLine, ...] = ()


@dataclass(frozen=True)
class RecipeDraftItem:
    ingredient_name: str
    unit: str
    usage_per_menu: Decimal


@dataclass(frozen=True)
class RecipeDraftMenu:
    menu_name: str
    items: tuple[RecipeDraftItem, ...]


@dataclass(frozen=True)
class RecipeDraft:
    menus: tuple[RecipeDraftMenu, ...]


@dataclass(frozen=True)
class GeoPoint:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class HourlyWeather:
    at: datetime
    temperature_c: Decimal | None
    condition: str | None


@dataclass(frozen=True)
class DayWeather:
    hours: tuple[HourlyWeather, ...]
    daily_max_c: Decimal | None


@dataclass(frozen=True)
class WeatherObservation:
    observed_on: date
    max_temperature_c: Decimal
    precipitation_mm: Decimal


@dataclass(frozen=True)
class EventRecord:
    id: str
    name: str
    place: str | None
    latitude: float
    longitude: float
    start_at: datetime
    end_at: datetime
    detail_url: str | None
    category: str | None = None


@dataclass(frozen=True)
class CrowdPlace:
    code: str
    name: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class CrowdHour:
    at: datetime
    level: str
    population: int


class ColumnDetector(Protocol):
    async def detect_columns(self, header: list[str], sample_rows: list[list[str]]) -> ColumnDetection: ...


class ReceiptReader(Protocol):
    async def read_receipt(self, jpeg: bytes, ingredient_units: dict[str, str]) -> ReceiptReading: ...


class RecipeDrafter(Protocol):
    async def draft_recipes(
        self, category: str, menu_names: list[str], ingredient_units: dict[str, str]
    ) -> RecipeDraft: ...


class Geocoder(Protocol):
    async def geocode(self, road_address: str) -> GeoPoint | None: ...


class WeatherProvider(Protocol):
    async def day_weather(self, point: GeoPoint, target_date: date, now: datetime) -> DayWeather: ...


class ObservationProvider(Protocol):
    async def daily_observations(self, start: date, end: date) -> list[WeatherObservation]: ...


class EventProvider(Protocol):
    async def events_on(self, target_date: date) -> list[EventRecord]: ...


class CrowdProvider(Protocol):
    def places(self) -> list[CrowdPlace]: ...

    async def forecast(self, place: CrowdPlace) -> list[CrowdHour]: ...


class PushSender(Protocol):
    async def send(self, subscription_json: str, title: str, body: str, url: str) -> bool:
        """보냈으면 True, 구독이 끝났으면(404/410) False."""
        ...


@dataclass
class Providers:
    columns: ColumnDetector
    receipts: ReceiptReader
    recipes: RecipeDrafter
    geocoder: Geocoder
    weather: WeatherProvider
    observations: ObservationProvider
    events: EventProvider
    crowd: CrowdProvider
    push: PushSender
