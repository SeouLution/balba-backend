# getDiscountRecommendations

`GET /stores/{storeId}/discount-recommendations`

4.0 할인 탭 상단 카드 · 4.0-2 할인 추천 목록

## 공개 계약

- operationId: `getDiscountRecommendations`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `DiscountRecommendations` |

## 책임과 경계

discounts / `RecommendDiscounts.execute(StoreContext, applyBaseTime=true) → DiscountRecommendationsView`. 저장된 추천을 읽지 않고 요청할 때 계산한다(D25). 계산 규칙은 [C06](../domain/calculations.md)이다.

## 처리 순서

1. store를 확인한다. `date`는 요청 시각의 KST 오늘, `baseTime`은 `policy.discount.base_time`(17:00)이다.
2. 요청 시각이 baseTime 전이거나 오늘이 휴무면 `recommendations=[]`, `surplusStock=[]`로 200.
3. 한 읽기 트랜잭션에서 오늘 끝 기준 재고(C04), 레시피, 메뉴 판매가, 최근 단가, 영업시간, 내일 예측을 읽어 C06으로 계산한다.
4. `recommendations`: id(`rec_<date>_<menuId>`), menu, unit, discount, startTime, quantity, reasonText, expectedSavings(양의 정수). 고른 순서대로 돌려준다.
5. `surplusStock`: C06의 표대로 ingredientId 순. quantity는 사용 단위다.
6. 200. DB에 쓰지 않고 외부 서비스를 부르지 않는다.

재고·판매가·단가·유통기한 중 모르는 것이 있어 계산할 수 없는 재료와 메뉴는 결과에서 빠진다. 그 결과 빈 목록이 될 수 있다.

## 완료 조건

16:59/17:00, 휴무, 판매가·단가·유통기한을 모르는 경우, 재고 null과 0의 구분, 같은 임박 재고를 두 추천이 나눠 쓰는 경우(F03), 추천 시작 시각, ORDER_REDUCED 조건, 조회 전후 DB 행 수 불변, 다른 store 격리를 검증한다. 같은 데이터로 두 번 조회하면 같은 결과와 같은 id가 나온다.
