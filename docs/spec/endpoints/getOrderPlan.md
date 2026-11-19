# getOrderPlan

`GET /stores/{storeId}/order-plans/{targetDate}`

2.8 행사 반영 발주 · 3.8 상단 '내일 발주'

## 공개 계약

- operationId: `getOrderPlan`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `targetDate` (path): {examples: ['2026-11-11'], format: date, type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `OrderPlan` |

## 책임과 경계

ordering / `GetOrderPlan.execute(StoreContext, targetDate) → OrderPlanSnapshot`. `ordering.PlanOrder`를 그대로 호출하고 응답으로 바꾼다. getHome과 adjustForecast도 같은 `PlanOrder`를 쓴다.

## 처리 순서

1. store 실존을 확인한다. targetDate는 오늘 또는 내일만 받는다(D16, 아니면 422). targetDate가 휴무면 `suppliers=[]`, `events=[]`, `usualTotal=null`로 200.
2. `PlanOrder(targetDate)`가 [C05](../domain/calculations.md)대로 계산한다: CORE 재료 필요량, targetDate 전날 끝 재고(C04), 최근 거래처·단가(C03), 오늘이 주문 요일인 거래처, 구매 팩 올림, 행사 증분.
3. supplierId 순으로 묶고 그 안은 ingredientId 순이다. 줄마다 ingredient, quantity, price가 필수이고 price는 `{unitPrice:null}`일 수 있다. `stock`은 구매 단위 Quantity이고 계산할 수 없으면 null이다. `eventDiff`는 quantity와 같은 단위다.
4. `events`는 준비량 계수에 반영한 행사, 반영한 것이 없으면 빈 배열. `usualTotal`은 C05의 평소 계획 합계 또는 null.
5. 200. 줄 금액과 현재 합계는 프론트가 `quantity.value × price.unitPrice`로 계산한다.

거래처가 하나도 없으면(영수증을 건너뛴 직후) `suppliers=[]`다(D20).

## 완료 조건

계란 환산·팩 올림(F02), 일부 단가만 아는 경우, stock null과 0의 구분, 주문 요일 UNKNOWN 거래처 제외, 최근 거래처를 다른 거래처로 바꾸지 않는 것, 오늘 주문 요일과 내일 사용일의 분리, 행사 증분 0 사례, 거래처 없음을 검증한다. 이 GET은 발주서를 만들지 않는다.
