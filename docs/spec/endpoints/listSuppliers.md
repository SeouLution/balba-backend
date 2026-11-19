# listSuppliers

`GET /stores/{storeId}/suppliers`

3.7 거래처 주문 요일

## 공개 계약

- operationId: `listSuppliers`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `{ items: List<Supplier> }` |

## 책임과 경계

suppliers / `ListSuppliers.execute(StoreId) → List<SupplierView>`.

## 처리 순서

store를 확인하고 supplierId 순으로 조회한다. 각 거래처의 id, name, orderDays(MON~SUN 순), orderDaysStatus, receiptCount를 돌려준다. `receiptCount`는 그 거래처 receipt_line의 서로 다른 `purchased_on` 수다. ERD 정의상 영수증 1장은 같은 거래처·같은 날짜 묶음이다(D22).

## 추정 규칙 (D15)

영수증을 확정할 때 CONFIRMED가 아닌 거래처만 추정한다. 그 거래처의 서로 다른 영수증 날짜가 `policy.supplier.order_days_min_receipt_dates`(3) 이상이면 그 날짜들의 요일 집합을 넣고 ESTIMATED. 그 미만이면 빈 배열, UNKNOWN. 사장님이 빈 요일로 저장한 CONFIRMED는 '주문 요일 없음'으로 그대로 둔다. GET은 상태를 바꾸지 않는다.

## 완료 조건

날짜 0/1/3개, 같은 날 영수증 여러 장이 1로 세어지는 것, CONFIRMED 보존, 정렬, 빈 배열을 검증한다. 공개 Supplier에 전화번호를 넣지 않는다.
