# listIngredients

`GET /stores/{storeId}/ingredients`

3.2 재료 탭

## 공개 계약

- operationId: `listIngredients`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `status` (query): {enum: [CORE, LATER], type: string} — 선택

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `IngredientList` |

## 책임과 경계

views / `ListIngredients.execute(StoreContext, status:CORE|LATER?) → IngredientListView`. 재료(catalog), 최근 단가·거래처(receipts PriceReader)를 조합한다. catalog가 receipts를 읽지 않게 조합을 views에 둔다.

## 처리 순서

1. store를 확인한다. `counts`는 필터와 관계없이 이 가게 전체의 CORE/LATER 수다.
2. status가 있으면 items만 거른다. 정렬은 CORE 먼저, 그 안에서 쓰이는 메뉴 수 내림차순, ingredientId 순(1.4 후보와 같은 순서).
3. `usageUnit`을 모르면 키를 생략한다. 구매 단위 이름이 없으면 `purchaseUnit=null`, 이름은 있고 환산 비율이 없으면 `{label, contentQuantity:null}`. 영수증 전에는 레시피 초안이 추정한 값이 들어 있다.
4. 확정된 영수증 줄이 없는 재료는 `price=null`, `supplier=null`. 줄은 있는데 단가가 없으면 `price={unitPrice:null}`이고 supplier는 그 줄의 거래처다(C03).
5. 200 `{items, counts:{core, later}}`. 남은 일수 필드는 스키마에 없으므로 넣지 않는다.

## 완료 조건

필터해도 counts 유지, 빈 목록, 최근 줄 단가 null일 때 과거 단가를 쓰지 않는 것, null과 생략의 차이, 다른 store 재료 누출 없음을 검증한다.
