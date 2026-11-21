# listPurchaseOrders

`GET /stores/{storeId}/purchase-orders`

3.8 지난 발주서

## 공개 계약

- operationId: `listPurchaseOrders`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `{ items: List<PurchaseOrderSummary> }` |

## 책임과 경계

ordering / `ListPurchaseOrders.execute(StoreId) → List<PurchaseOrderSummary>`. supplier JOIN으로 store를 제한한다.

## 처리 순서

store 확인 후 purchase_order를 created_at DESC,id DESC로 읽는다. id,targetDate,supplier{id,name},totalAmount를 투영한다. totalAmount는 생성 시 저장값이며 현 시점 단가로 다시 계산하지 않는다. supplier 이름은 현재 값이지만 이름 변경 endpoint는 현재 범위에 없다. 빈 목록은 `{items:[]}`다.

## 읽기·실패

보냈는지와 무관하게 생성된 발주서 전체를 반환한다. API에 pagination/range가 없으므로 임의 상위 20건으로 잘라내지 않는다. 전체 데이터 증가에 따른 pagination은 후속 OpenAPI 변경이다. 상세/재열기 endpoint를 임의 구현하지 않는다.

## 완료 조건

동일 created_at의 안정 정렬, 단가 변동 후 과거 totalAmount 고정, 미전송 포함, 타 store 격리, 빈 배열을 검증한다.
