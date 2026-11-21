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
| 200 | `{ items: List<Recipe> }` |

## 책임과 경계

catalog / `ListRecipes.execute(StoreId) → List<RecipeSnapshot>`. 1.6 화면용이며 **핵심(CORE) 재료 항목만** 준다(D21).

## 처리 순서

1. store를 확인한다. 가장 최근 GENERATE_RECIPES 작업이 DEAD면 500. GENERATING이면 `items=[]`.
2. 모든 메뉴를 menuId 순으로 돌려준다. 각 메뉴의 `items`는 그 메뉴 레시피 중 CORE 재료 항목만, ingredientId 순이다. CORE 재료 항목이 없는 메뉴도 `items=[]`로 포함한다.
3. 각 항목의 quantity는 메뉴 1단위당 사용량이고 unit은 재료의 **사용 단위**다. 구매 단위로 바꾸지 않는다.

## 읽기·실패

읽기 전용. 사용 단위가 없는 CORE 재료 항목은 내보낼 수 없으므로 그 항목만 빼고 로그에 남긴다. LATER 재료의 레시피는 이 응답에 없지만 서버에 남아 있고 재고 계산에 쓰인다.

## 완료 조건

CORE만 반환, CORE를 바꾼 뒤 응답 변화, 메뉴/재료의 소속, 사용 단위 유지, 0 사용량, N+1 없는 조회를 검증한다.
