# setCoreIngredients

`PUT /stores/{storeId}/core-ingredients`

1.4 핵심 재료 확정 ('N개로 시작하기')

## 공개 계약

- operationId: `setCoreIngredients`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문: `application/json`, 필수, 인라인 타입 (아래).

```yaml
type: object
required:
- ingredientIds
properties:
  ingredientIds:
    type: array
    minItems: 1
    items:
      type: string
```

| 성공 상태 | 응답 |
|---|---|
| 204 | 본문 없음 |

## 책임과 경계

catalog / `SetCoreIngredients.execute(StoreContext, selected:List<IngredientId>) → void`. 요청 목록은 **전체 CORE 집합의 교체**다.

## 처리 순서

1. 길이≥1, 중복 ID 없음. store를 잠그고 `generation_status=READY`인지 확인한다. GENERATING이면 422.
2. 전달된 ID가 모두 이 가게의 재료인지 확인한다. 하나라도 없거나 다른 가게 것이면 404이고 아무것도 바꾸지 않는다.
3. 선택된 재료는 CORE, 이 가게의 나머지 재료는 모두 LATER로 바꾼다. recipe_item은 지우지 않는다.
4. commit 후 204. 같은 집합을 순서만 바꿔 다시 보내도 결과가 같다. 발주·알림은 조회할 때 계산하므로 따로 갱신할 것이 없다.

## 완료 조건

집합 교체, 잘못된 ID 하나로 전체 rollback, 다른 store 보호, 반복 PUT, GENERATING 중 422를 검증한다. LATER 재료의 레시피는 남아서 재고 계산에 쓰인다.
