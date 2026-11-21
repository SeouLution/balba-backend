# saveRecipes

`PUT /stores/{storeId}/recipes`

1.6 '저장하고 홈으로'

## 공개 계약

- operationId: `saveRecipes`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문: `application/json`, 필수, `RecipeBatchSave`.

| 성공 상태 | 응답 |
|---|---|
| 204 | 본문 없음 |

## 책임과 경계

catalog / `SaveRecipes.execute(StoreContext, List<RecipeReplacement{menuId,items:List<{ingredientId,quantity:NonNegativeQty}>}>) → void`. 핵심(CORE) 재료 항목만 다룬다(D21).

## 처리 순서

1. 바깥 items≥1, menuId 중복 없음, 메뉴 안 ingredientId 중복 없음. store 잠금 후 READY 확인(GENERATING이면 422).
2. 모든 menuId/ingredientId가 이 가게 것인지 확인한다(아니면 404). 요청의 재료는 모두 CORE여야 한다. LATER 재료가 오면 422.
3. **요청에 포함된 메뉴마다, 그 메뉴 레시피의 CORE 재료 항목만** 요청 내용으로 교체한다. 그 메뉴의 LATER 재료 항목과 요청에 없는 메뉴는 그대로 둔다. 메뉴의 `items=[]`는 그 메뉴의 CORE 재료 항목을 모두 지운다. 0 수량 항목은 저장한다.
4. quantity는 메뉴 1인분당 사용량이고 재료 단위(개 또는 g) 기준이다. 단위를 바꾸는 요청이 아니다.
5. commit 후 204. 예측은 판매 기록으로 만들므로 레시피 저장으로 다시 만들지 않는다. 발주·재고는 다음 조회에서 새 레시피로 계산된다.

## 원자성·실패

메뉴 하나라도 잘못되면 전체 rollback. 없는 ID는 404, LATER 재료·GENERATING은 422.

## 완료 조건

메뉴별 CORE 항목 교체, LATER 항목 보존, 요청에 없는 메뉴 보존, 빈 items, 전체 rollback, 같은 요청 반복을 검증한다.
