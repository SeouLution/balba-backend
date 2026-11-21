import math
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
QTY_STEP = Decimal("0.000001")
ONE = Decimal(1)


class InvalidRequest(Exception):
    pass


class NotFound(Exception):
    pass


class InternalFailure(Exception):
    pass


class Unauthorized(Exception):
    pass


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class StoreContext:
    store_id: str
    now: datetime
    local_date: date

    @property
    def local_now(self) -> datetime:
        return self.now.astimezone(KST)


@dataclass(frozen=True)
class DayHours:
    day: str
    closed: bool
    open: time | None = None
    close: time | None = None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def day_code(day: date) -> str:
    return DAYS[day.weekday()]


def sort_days(days) -> list[str]:
    return sorted(set(days), key=DAYS.index)


def to_qty(value) -> Decimal:
    """요청 수량을 소수 6자리 Decimal로 바꾼다. bool·NaN·Infinity·음수는 거절한다."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("quantity must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("quantity must be finite")
    quantity = Decimal(str(value))
    if not quantity.is_finite() or quantity < 0:
        raise ValueError("quantity must be a finite non-negative number")
    return quantity.quantize(QTY_STEP, rounding=ROUND_HALF_UP)


def round_half_up(value: Decimal) -> int:
    return int(Decimal(value).quantize(ONE, rounding=ROUND_HALF_UP))


def ceil_int(value: Decimal) -> int:
    return int(Decimal(value).quantize(ONE, rounding=ROUND_CEILING))


def floor_int(value: Decimal) -> int:
    return int(Decimal(value).quantize(ONE, rounding=ROUND_FLOOR))


def num(value: Decimal | int | None):
    """Decimal을 JSON number로 내보낸다. 문자열로 직렬화되지 않게 한다."""
    if value is None or isinstance(value, int):
        return value
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def normalize_name(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


def parse_hhmm(text: str) -> time:
    return time(int(text[:2]), int(text[3:5]))


def hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def hours_for(business_hours: tuple[DayHours, ...], day: date) -> DayHours:
    return business_hours[day.weekday()]


def business_window(business_hours: tuple[DayHours, ...], day: date) -> tuple[datetime, datetime] | None:
    """영업일의 [시작, 종료) 구간. open > close면 익일 마감이다. 휴무면 None."""
    hours = hours_for(business_hours, day)
    if hours.closed:
        return None
    start = datetime.combine(day, hours.open, KST)
    end = datetime.combine(day, hours.close, KST)
    if hours.open > hours.close:
        end += timedelta(days=1)
    return start, end


def kst_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), KST)
