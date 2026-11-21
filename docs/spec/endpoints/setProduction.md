# setProduction

`PUT /stores/{storeId}/production/{date}/menus/{menuId}`

2.2 '만듦 +1' · 2.3 '기록하기'

## 공개 계약

- operationId: `setProduction`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `date` (path): {examples: ['2026-11-11'], format: date, type: string} — 필수
- `menuId` (path): {type: string} — 필수

요청 본문: `application/json`, 필수, [OpenAPI](../../../openapi.yaml)의 `setProduction → requestBody → application/json`.

| 성공 상태 | 응답 |
|---|---|
| 204 | 본문 없음 |

## 책임과 경계

forecasting / `SetProduction.execute(StoreContext, date, menuId, quantity:NonNegativeQty) → void`. produced는 지금까지 만든 양의 누적 절대값이다.

## 처리 순서

1. store/menu 소속과 그 날짜·메뉴의 forecast 행을 확인한다(없으면 404). date는 오늘만 받는다(아니면 422).
2. store를 잠그고 `produced`를 요청 수량으로 교체한다. 이전 값보다 작아도 정정으로 받는다. 0도 저장한다.
3. commit 후 204. recommended/adjusted/daily_sales/receipt_line은 바꾸지 않는다.

## 재고에 미치는 영향

판매 기록이 없는 날의 재료 사용량은 만든 양으로 계산한다([C04](../domain/calculations.md), D03). 그래서 이 값은 다음 조회부터 재고와 발주의 stock에 반영된다. 할인 추천 수량도 이 값에서 추천 준비량을 뺀 값이다([C06](../domain/calculations.md), D37). 기록하지 않으면 최종 준비량(planned)을 만든 것으로 본다.

## 실패·재요청

store/menu/forecast 없음은 404, 날짜/수량 오류는 422. 같은 PUT을 두 번 보내도 늘어나지 않는다. '만듦 +1'은 프론트가 현재값+1을 보낸다. 여러 기기가 같은 값을 읽고 각자 +1을 보내면 하나가 사라지는 것은 현재 계약의 한계다.

## 완료 조건

50을 두 번 보내도 50, 50→0 정정, TODAY 홈에 produced 반영, 내일/어제 날짜 거절, daily_sales 불변, produced를 바꾸면 그날 재고 계산이 달라지는 것(F02)을 검증한다.
