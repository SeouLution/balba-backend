# getHome

`GET /stores/{storeId}/home`

2.0-1 온보딩 직후 · 2.2 당일 아침 · 2.7-1 행사 펼친 상태 · 2.9 행사 당일 아침

## 공개 계약

- operationId: `getHome`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `Home` |

## 책임과 경계

views / `GetHome.execute(StoreContext) → HomeResponse`. ForecastReader, ContextReader, `ordering.PlanOrder`의 결과를 조합한다. 발주·재고 규칙을 여기서 다시 구현하지 않는다.

## 처리 순서

1. store 실존을 확인하고 [C01](../domain/calculations.md)로 view/targetDate를 정한다.
2. 한 읽기 트랜잭션에서 메뉴, targetDate 예측, 날씨, 반경 내 행사를 읽는다.
3. `tiles`: 저장된 날씨가 없으면 `weather` 키를 생략한다. `events`는 반경 내 행사 목록이며 없으면 빈 배열이다. 행사는 [공통 EventSummary 규칙](../03-http-contract.md)에 따라 변환한다.
4. `forecast.items`는 예측이 있는 메뉴 중 상위 3개(C02), `totalMenuCount`는 전체 메뉴 수. 예측이 없으면 `items=[]`. `planned`는 adjusted 우선이고 0도 그대로 준다. `produced`와 `previousRecommended`는 TODAY에서만 값을 주고 TOMORROW에서는 null이다.
5. `order`: TOMORROW에서 `PlanOrder(targetDate)` 결과에 줄이 있으면 `{supplierCount: 거래처 수, estimatedTotal: C05 금액 규칙}`, 줄이 없으면 null. TODAY에서는 `order` 키를 생략한다.
6. `banners`: [C07](../domain/calculations.md) 조건에 맞는 것만 FIRST_FORECAST, OVERNIGHT_CHANGE, EVENT_TODAY, DELIVERY_EXPECTED 순으로 넣는다.
7. `businessHours`에 가게의 월~일 영업시간을 담아 `storeName`, `view`, `targetDate`, `tiles`, `banners`, `forecast`와 함께 반환한다. 휴무일에는 `day`와 `closed`만 포함한다.

## 실패·읽기 일관성

store 없음 404, DB 오류 500. GET에서 외부 API 호출, 작업 등록, 예측 계산·저장을 하지 않는다. 예측은 저장된 값을 읽고, 발주 요약은 같은 읽기 트랜잭션의 데이터로 계산한다.

## 완료 조건

19:59/20:00/23:59/00:00 KST, `DEMO_NOW`, 휴무, 온보딩 직후(order=null), 영수증 확정 뒤 order, 행사 없음·반경 경계, 일부 단가만 아는 경우의 합계, 배너 4종의 조건·문구를 검증한다. getOrderPlan과 같은 시각에 호출하면 거래처 수와 합계가 일치한다.
