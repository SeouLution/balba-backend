# listIngredientCandidates

`GET /stores/{storeId}/ingredient-candidates`

1.4 핵심 재료 고르기

## 공개 계약

- operationId: `listIngredientCandidates`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `{ generationStatus: string, items: List<IngredientCandidate> }` |

## 책임과 경계

catalog / `ListIngredientCandidates.execute(StoreId) → CandidateList{generationStatus,items}`.

## 처리 순서

1. store 실존을 확인한다. 이 가게의 가장 최근 GENERATE_RECIPES 작업이 DEAD면 500.
2. GENERATING이면 `items=[]`를 돌려준다. 만들다 만 후보를 내보내지 않는다. 엑셀을 올리기 전에도 같다.
3. READY면 모든 재료를 쓰이는 메뉴 수 내림차순, ingredientId 순으로 정렬한다(CORE·LATER 모두). `usedInMenus`는 recipe_item의 서로 다른 메뉴를 menuId 순으로, `usedInMenuCount`는 그 길이다.
4. `tags`는 저장된 `ingredient.tags`를 그대로 준다. '추천만' 필터는 프론트가 한다.

## 배지 규칙

GENERATE_RECIPES 작업이 끝날 때 이 가게 모든 재료의 `tags`를 [정책 파일](../../../app/policies/defaults.toml) `[tags]`로 다시 계산해 저장한다. 1.4는 영수증을 찍기 전 화면이므로 단가를 쓰지 않는다.

- EXPENSIVE: 재료 이름에 `expensive_keywords` 중 하나가 들어 있음.
- RECOMMENDED: 쓰이는 메뉴 수가 `recommended_min_menus` 이상이거나 EXPENSIVE에 해당.

## 원자성·실패

읽기 전용. 같은 가게의 재료와 메뉴만 돌려준다.

## 완료 조건

업로드 전/생성 중/완료/DEAD, usedInMenuCount 중복 제거, 동률 정렬, 배지 2종의 조건, 다른 store 누출 없음을 검증한다.
