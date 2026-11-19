# adjustForecast

`PUT /stores/{storeId}/forecasts/{date}/menus/{menuId}`

2.0-1 · 2.7-1 준비량 +/−

## 공개 계약

- operationId: `adjustForecast`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `date` (path): {examples: ['2026-11-11'], format: date, type: string} — 필수
- `menuId` (path): {type: string} — 필수

요청 본문: `application/json`, 필수, [OpenAPI](../../../openapi.yaml)의 `adjustForecast → requestBody → application/json`.

| 성공 상태 | 응답 |
|---|---|
| 200 | `ForecastAdjustmentResult` |

## 책임과 경계

views / `AdjustForecast.execute(StoreContext, date, menuId, quantity:NonNegativeQty) → ForecastAdjustmentResult`. 준비량 저장은 forecasting의 쓰기 포트로, 발주 요약은 `ordering.PlanOrder`로 한다. forecasting이 ordering을 부르지 않게 조합을 views에 둔다.

## 처리 순서

1. store/menu 소속을 확인한다. date는 오늘 또는 내일만 받는다(D16, 아니면 422). 그 날짜·메뉴의 forecast 행이 없으면 404다. 없는 예측을 수량만으로 만들지 않는다.
2. store를 잠그고 quantity를 `adjusted`에 **절대값**으로 저장한다. 0도 정상 값이며 null과 다르다. recommended/baseline/produced는 바꾸지 않는다.
3. 같은 트랜잭션에서 `PlanOrder(date)`를 실행해 방금 저장한 준비량이 반영된 발주 계획을 얻는다.
4. `item`은 저장 후의 ForecastItem이다. `order`는 date가 내일이면 getHome과 같은 규칙의 요약(줄이 없으면 null), date가 오늘이면 null이다.
5. commit 후 200. 발주 계산에서 예상하지 못한 오류가 나면 전체 rollback한다.

## 경합·재요청

같은 quantity를 다시 보내도 결과가 같다. 동시에 들어온 조정은 나중에 잠금을 얻은 값이 남는다. 예측 작업은 `adjusted`를 쓰지 않는다. 사용자가 고친 값을 지우는(null로 되돌리는) API는 없다.

## 완료 조건

quantity=0, 소수 반올림, 음수/NaN, 없는 forecast, 다른 가게 menu, 단가를 모르는 줄이 있을 때의 합계, 고친 메뉴의 행사 증분 0, 예측 작업과의 경합, 응답 order가 직후 getHome의 order와 같은 것을 검증한다.
