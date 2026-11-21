# listRecipes

`GET /stores/{storeId}/recipes`

1.6 레시피 초안

## 공개 계약

- operationId: `listRecipes`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `{ generationStatus: string, items: List<Recipe> }` |

## 책임과 경계

catalog / `ListRecipes.execute(StoreId) → RecipeList{generationStatus,items}`. 1.6 화면용이며 메뉴의 모든 재료 항목을 준다(D52). 프론트는 `generationStatus`가 READY가 될 때까지 이 조회를 반복한다.

## 처리 순서

1. store를 확인한다. 가장 최근 GENERATE_RECIPES 작업이 DEAD면 500. GENERATING이면 `generationStatus=GENERATING`, `items=[]`다. 만들다 만 레시피를 내보내지 않는다. 엑셀을 올리기 전에도 같다.
2. READY면 모든 메뉴를 menuId 순으로 돌려준다. 각 메뉴의 `items`는 그 메뉴 레시피의 모든 재료 항목이고 ingredientId 순이다. 재료 항목이 없는 메뉴도 `items=[]`로 포함한다.
3. 각 항목의 quantity는 메뉴 1인분당 사용량이고 unit은 재료 단위(`개` 또는 `g`)다. 메뉴의 `unit`은 항상 `인분`이다(D46).

## 읽기·실패

읽기 전용. 같은 가게의 메뉴와 재료만 돌려준다.

## 완료 조건

업로드 전/생성 중/완료/DEAD, 메뉴/재료의 소속, 재료 단위(개·g)와 메뉴 단위(인분), 0 사용량, N+1 없는 조회를 검증한다.
