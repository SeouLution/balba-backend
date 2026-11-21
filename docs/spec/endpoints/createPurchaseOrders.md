# createPurchaseOrders

`POST /stores/{storeId}/purchase-orders`

2.8 '발주서 만들기'

## 공개 계약

- operationId: `createPurchaseOrders`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문: `application/json`, 필수, `PurchaseOrderCreate`.

| 성공 상태 | 응답 |
|---|---|
| 201 | `{ items: List<PurchaseOrder> }` |

## 책임과 경계

ordering / `CreatePurchaseOrders.execute(StoreContext, PurchaseOrderBatchInput) → List<PurchaseOrderSnapshot>`. 요청 quantity는 재료 단위(개 또는 g)다.

## 처리 순서

1. suppliers≥1, supplierId 중복 없음, 거래처 안 ingredientId 중복 없음. 같은 재료를 여러 거래처에 넣는 것도 422.
2. store를 잠근다. targetDate는 오늘 또는 내일이고 영업일이어야 한다. supplier/ingredient가 모두 이 가게 것인지 확인한다(아니면 404). 각 줄의 거래처는 그 재료의 최근 거래처(C03)이고 오늘이 주문 요일이어야 한다. 재료는 CORE여야 한다. 어긋나면 422.
3. 0 수량 줄은 뺀다. 줄이 남지 않은 거래처가 있거나 전체가 비면 422. 단가를 몰라도 만들 수 있다.
4. 거래처마다 purchase_order 1개와 줄(재료, 수량)을 저장한다. `total_amount`는 [C05](../domain/calculations.md) 금액 규칙(단가를 아는 줄만 합산, 하나도 없으면 null)으로 계산해 저장한다. 이후 단가가 바뀌어도 다시 계산하지 않는다.
5. `message_text`를 [C07](../domain/calculations.md) 발주서 템플릿으로 만들어 저장한다.
6. supplierId 순으로 PurchaseOrder를 돌려준다. `supplier.phone`, `phoneConfirmed`는 거래처 값 그대로, `phoneSourceText`는 C07 템플릿을 따른다.
7. 모든 거래처·줄을 한 번에 commit하고 201 `{items:[...]}`. 전송이나 입고는 하지 않는다.

## 원자성·실패

거래처 하나라도 오류면 전체 rollback. 없는/다른 store ID는 404, 관계·요일·수량 문제는 422. 요청 수량이 최종이며 추천값으로 덮지 않는다. 같은 POST를 다시 보내면 새 발주서가 생긴다.

## 완료 조건

여러 거래처 원자성, 0·소수 수량, 단가 모름, 다른 store와 다른 거래처의 재료, 문구의 날짜·줄·끝줄, 요청 수량 유지, 전송·입고가 일어나지 않는 것을 검증한다.
