# saveOrderDays

`PUT /stores/{storeId}/suppliers/order-days`

3.7 '저장'

## 공개 계약

- operationId: `saveOrderDays`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문: `application/json`, 필수, [OpenAPI](../../../openapi.yaml)의 `saveOrderDays → requestBody → application/json`.

| 성공 상태 | 응답 |
|---|---|
| 204 | 본문 없음 |

## 책임과 경계

suppliers / `SaveOrderDays.execute(StoreContext, List<{supplierId,orderDays:List<DayOfWeek>}>) → void`.

## 처리 순서

1. supplierId 중복과 orderDays 안의 중복은 422. OpenAPI에 바깥 minItems가 없으므로 `items=[]`는 아무것도 바꾸지 않는 정상 요청이다.
2. store를 잠그고 모든 supplier가 이 가게 것인지 확인한다. 하나라도 없거나 다른 가게 것이면 404이고 전체 rollback.
3. 요청에 포함된 거래처만 `order_days`를 MON~SUN 순으로 교체하고 `order_days_status=CONFIRMED`로 바꾼다. `orderDays=[]`도 확정된 빈 요일로 저장한다. 요청에 없는 거래처는 그대로 둔다.
4. commit 후 204 빈 body. 발주 계획은 조회할 때 계산하므로 다음 getOrderPlan부터 반영된다. 이후 영수증 확정의 요일 추정은 CONFIRMED를 덮지 않는다.

## 완료 조건

일부 거래처만 보내기, 빈 items, 빈 요일, 중복 요일, 전체 rollback, 저장 뒤 영수증 확정에도 CONFIRMED 유지, 같은 PUT 반복, 다음 OrderPlan의 요일 필터 반영을 검증한다.
