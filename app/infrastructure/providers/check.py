"""실제 키로 공급자를 한 번씩 호출해 본다. 키 값은 출력하지 않는다.

실행: python -m app.infrastructure.providers.check
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from os import environ, getenv
from pathlib import Path

import httpx

from app.infrastructure.providers import real
from app.infrastructure.providers.ports import GeoPoint
from app.shared.types import KST

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "providers"
SAMPLE_ADDRESS = "서울 마포구 월드컵로 212"
SAMPLE_POINT = GeoPoint(37.5663, 126.9019)
SAMPLE_HEADER = ["주문일자", "상품명", "수량", "결제금액"]
SAMPLE_ROWS = [["2026-11-19", "김밥", "3", "12000"], ["2026-11-19", "떡볶이", "1", "5000"]]


async def check_claude(providers) -> str:
    detection = await providers.columns.detect_columns(SAMPLE_HEADER, SAMPLE_ROWS)
    return f"열 판별 {detection.date}/{detection.menu_name}/{detection.quantity}/{detection.amount}"


async def check_kakao(providers) -> str:
    headers = {"Authorization": f"KakaoAK {environ['KAKAO_REST_API_KEY']}"}
    async with httpx.AsyncClient(timeout=real.HTTP_TIMEOUT) as client:
        response = await real.get_with_retry(
            client, real.KAKAO_ADDRESS_URL, params={"query": SAMPLE_ADDRESS}, headers=headers
        )
    (FIXTURES / "kakao_address_live.json").write_text(response.text, encoding="utf-8")
    return f"좌표 {real.parse_kakao_address(response.json())}"


async def check_kma(providers) -> str:
    now = datetime.now(UTC)
    tomorrow = now.astimezone(KST).date() + timedelta(days=1)
    grid_x, grid_y = real.to_kma_grid(SAMPLE_POINT.latitude, SAMPLE_POINT.longitude)
    base_date, base_time = real.latest_kma_base(now)
    params = {
        "serviceKey": environ["KMA_SERVICE_KEY"],
        "pageNo": 1,
        "numOfRows": 30,
        "dataType": "XML",
        "base_date": base_date,
        "base_time": base_time,
        "nx": grid_x,
        "ny": grid_y,
    }
    async with httpx.AsyncClient(timeout=real.HTTP_TIMEOUT) as client:
        response = await real.get_with_retry(client, real.KMA_FORECAST_URL, params=params)
    (FIXTURES / "kma_vilage_fcst_live.xml").write_text(response.text, encoding="utf-8")
    real.parse_kma_items(response.text)
    day = await providers.weather.day_weather(SAMPLE_POINT, tomorrow, now)
    return f"내일 시간별 예보 {len(day.hours)}건, 최고기온 {day.daily_max_c}"


async def check_seoul_events(providers) -> str:
    today = datetime.now(KST).date()
    return f"오늘 열리는 행사 {len(await providers.events.events_on(today))}건"


async def check_seoul_crowd(providers) -> str:
    place = providers.crowd.places()[0]
    return f"{place.name} 예측 인구 {len(await providers.crowd.forecast(place))}시간"


async def check_push(providers) -> str:
    subscription = getenv("PUSH_TEST_SUBSCRIPTION")
    if not subscription:
        return "건너뜀 (PUSH_TEST_SUBSCRIPTION에 브라우저 구독 JSON을 넣으면 실제로 보낸다)"
    delivered = await providers.push.send(subscription, "발주바주 점검", "푸시 연결을 확인했어요", "/")
    return "발송됨" if delivered else "구독이 만료됨(404/410)"


CHECKS = {
    "Claude API": check_claude,
    "카카오 주소 검색": check_kakao,
    "기상청 단기예보": check_kma,
    "서울 문화행사": check_seoul_events,
    "서울 도시데이터": check_seoul_crowd,
    "웹 푸시": check_push,
}


async def main() -> int:
    providers = real.real_providers(environ)
    failed = 0
    for name, check in CHECKS.items():
        try:
            print(f"[성공] {name}: {await check(providers)}")
        except Exception as error:
            failed += 1
            print(f"[실패] {name}: {type(error).__name__}: {error}")
    return failed


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
