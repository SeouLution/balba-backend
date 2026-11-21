"""서버가 만드는 문장 템플릿. 응답·푸시에 들어가는 문장은 여기서만 만든다."""

from datetime import date, datetime
from decimal import Decimal

from app.shared.types import KST, round_half_up

WEEKDAY_KO = "월화수목금토일"
CONDITION_KO = {
    "CLEAR": "맑음",
    "PARTLY_CLOUDY": "구름많음",
    "CLOUDY": "흐림",
    "RAIN": "비",
    "SNOW": "눈",
    "RAIN_SNOW": "비/눈",
    "SHOWER": "소나기",
}


def has_final_consonant(word: str) -> bool:
    last = word.strip()[-1:] if word.strip() else ""
    if "가" <= last <= "힣":
        return (ord(last) - ord("가")) % 28 != 0
    return last in "013678"


def josa(word: str, with_final: str, without_final: str) -> str:
    return f"{word}{with_final if has_final_consonant(word) else without_final}"


def month_day(day: date) -> str:
    return f"{day.month}/{day.day}"


def qty_text(value: Decimal | int) -> str:
    value = Decimal(value)
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def percent_text(factor: Decimal) -> str:
    return f"{round_half_up((factor - 1) * 100):+d}%"


def weather_text(condition: str, starts_at_hour: int | None) -> str:
    label = CONDITION_KO[condition]
    return label if starts_at_hour is None else f"{label} · {starts_at_hour}시부터"


def weather_change_text(night_text: str | None, new_text: str) -> str | None:
    if night_text is None or night_text == new_text:
        return None
    return f"{night_text} → {new_text}"


def forecast_summary(
    sample_weeks: int,
    target_date: date,
    baseline: Decimal,
    unit: str,
    event_factor: Decimal,
    weather_condition: str | None,
    weather_factor: Decimal,
) -> str:
    text = f"지난 {sample_weeks}주 {WEEKDAY_KO[target_date.weekday()]}요일 평균 {round_half_up(baseline)}{unit}"
    if event_factor != 1:
        text += f" · 행사 {percent_text(event_factor)}"
    if weather_factor != 1 and weather_condition is not None:
        text += f" · {CONDITION_KO[weather_condition]} {percent_text(weather_factor)}"
    return text


def first_forecast_banner(sales_weeks: int) -> tuple[str, str]:
    return "첫 예측이 준비됐어요", f"{sales_weeks}주치 판매 기록으로 계산했어요"


def overnight_change_title(menu_name: str | None, diff: Decimal, unit: str) -> str:
    if menu_name is None:
        return "밤사이 날씨 예보가 바뀌었어요"
    verb = "줄였어요" if diff < 0 else "늘렸어요"
    return f"밤사이 예보가 바뀌어 {josa(menu_name, '을', '를')} {qty_text(abs(diff))}{unit} {verb}"


def clock_text(moment: datetime) -> str:
    return moment.astimezone(KST).strftime("%H:%M")


def event_today_banner(
    start_at: datetime,
    end_at: datetime,
    peak_from_hour: int | None,
    peak_to_hour: int | None,
    top_menu_name: str | None,
) -> tuple[str, str | None]:
    title = f"오늘 근처 행사 · {clock_text(start_at)} ~ {clock_text(end_at)}"
    if peak_from_hour is None or peak_to_hour is None or top_menu_name is None:
        return title, None
    start_hour = start_at.astimezone(KST).hour
    message = f"{peak_from_hour}~{peak_to_hour}시가 가장 붐벼요 · {start_hour}시 전에 {top_menu_name} 준비를 끝내두세요"
    return title, message


def delivery_expected_banner(lines: list[tuple[str, Decimal, str]]) -> tuple[str, str]:
    shown = " · ".join(f"{name} {qty_text(quantity)}{unit}" for name, quantity, unit in lines[:3])
    if len(lines) > 3:
        shown += f" 외 {len(lines) - 3}개"
    return "어젯밤 발주한 재료가 오전에 들어와요", shown


def discount_reason(quantity: Decimal, unit: str) -> str:
    return f"예상보다 {qty_text(quantity)}{unit} 더 만들었어요"


def promo_text(menu_name: str) -> str:
    return f"갓 만든 {menu_name}, 마감 전에 할인해요"


def phone_source_text(read_on: date) -> str:
    return f"{month_day(read_on)} 영수증에서 읽었어요. 다음부터는 묻지 않아요"


def purchase_order_message(
    store_name: str,
    target_date: date,
    requested_on: date,
    open_hour: int,
    lines: list[tuple[str, Decimal, str]],
) -> str:
    arrival_day = "내일" if (target_date - requested_on).days == 1 else month_day(target_date)
    rows = [f"[{store_name}] {month_day(target_date)}({WEEKDAY_KO[target_date.weekday()]}) 발주"]
    rows += [f"{product_name} × {qty_text(quantity)}{unit}" for product_name, quantity, unit in lines]
    rows.append(f"{arrival_day} 오전 {open_hour}시 전 도착 부탁드립니다.")
    return "\n".join(rows)


def duplicate_receipt_notice(purchased_on: date, supplier_name: str) -> str:
    return f"{month_day(purchased_on)} {supplier_name} 영수증이 두 번 찍혀서 1장은 뺐어요"


def unreadable_photo_notice(photo_number: int) -> str:
    return f"{photo_number}번째 사진은 읽지 못해서 뺐어요"


def unreadable_lines_notice(purchased_on: date, supplier_name: str, line_count: int) -> str:
    return f"{month_day(purchased_on)} {supplier_name} 영수증에서 {line_count}줄은 읽지 못해서 뺐어요"


def push_message(slot: str, top_menu_name: str, planned: Decimal, unit: str, menu_count: int) -> tuple[str, str]:
    title = "내일 준비량이 나왔어요" if slot == "NIGHT" else "오늘 미리 만들어둘 양"
    return title, f"{top_menu_name} {qty_text(planned)}{unit} 등 {menu_count}개 메뉴"
