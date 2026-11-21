# listIngredients

`GET /stores/{storeId}/ingredients`

3.2 재료 탭

## 공개 계약

- operationId: `listIngredients`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `IngredientList` |

## 책임과 경계

views / `ListIngredients.execute(StoreContext) → IngredientListView`. 재료(catalog), 최근 단가·거래처(receipts PriceReader)를 조합한다. catalog가 receipts를 읽지 않게 조합을 views에 둔다.

## 처리 순서

1. store를 확인한다.
2. 이 가게의 모든 재료를 쓰이는 메뉴 수 내림차순, ingredientId 순으로 돌려준다(D52).
3. `unit`은 재료 단위(`개` 또는 `g`)이고 항상 있다(D47). 구매 단위와 환산 비율은 내보내지 않는다.
4. 확정된 영수증 줄도 단가 있는 수기 줄도 없는 재료는 `price=null`이고, 영수증 줄이 없으면 `supplier=null`이다. 단가를 정하는 줄은 있는데 단가가 없으면 `price={unitPrice:null}`이다. supplier는 최근 영수증 줄의 거래처다(C03, D58). `unitPrice`는 재료 단위 1개당 원이며 소수일 수 있다(D49).
5. 200 `{items}`. 남은 일수 필드는 스키마에 없으므로 넣지 않는다.

## 완료 조건

빈 목록, 정렬, 최근 줄 단가 null일 때 과거 단가를 쓰지 않는 것, null과 생략의 차이, 다른 store 재료 누출 없음을 검증한다.
