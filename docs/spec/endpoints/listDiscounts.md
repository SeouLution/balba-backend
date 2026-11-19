# listDiscounts

`GET /stores/{storeId}/discounts`

4.0 할인 기록 (오늘 · 이번 주 · 전체)

## 공개 계약

- operationId: `listDiscounts`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `range` (query): {default: TODAY, enum: [TODAY, THIS_WEEK, ALL], type: string} — 선택

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `DiscountHistory` |

## 책임과 경계

discounts / `ListDiscounts.execute(StoreContext, range:TODAY|THIS_WEEK|ALL=TODAY) → List<DiscountSnapshot>`.

## 처리 순서

1. store를 확인하고 요청 시각을 한 번 고정한다. TODAY는 오늘 KST 00:00 이상 내일 00:00 미만, THIS_WEEK는 이번 주 월요일 00:00 이상 요청시각 이하, ALL은 날짜 제한 없음이다.
2. 경계를 UTC Instant로 바꿔 created_at을 필터링하고 menu JOIN으로 store를 제한한다.
3. created_at DESC,id DESC로 조회한다. public date는 created_at의 KST 날짜이며 현재 날짜로 덮지 않는다.
4. 저장된 `original_price`, 할인 종류·값, `waste_cost_avoided`, `revenue_reduction`, `promo_text`를 그대로 쓴다. `discountedPrice`는 저장된 판매가와 할인 값으로 계산한다([C06](../domain/calculations.md)). 현재 메뉴 판매가로 다시 계산하지 않는다. `menu` 이름과 `unit`은 현재 메뉴 값, `channels`는 정책 파일 순서다. nullable DB waste_cost_avoided가 null이면 non-nullable 선택 필드 wasteCostAvoided는 생략한다.
5. 200 `{items}`. 실제 앱 등록 여부/전송 여부가 없으므로 설정 생성 이력이라고 해석한다. 전체를 반환하며 임의 페이지 제한을 넣지 않는다.

## 완료 조건

UTC↔KST 자정, 월요일/일요일 경계, default range, 동률 정렬, 메뉴 판매가가 바뀐 뒤에도 과거 금액 유지, preview가 목록에 없음, 타 store 격리를 검증한다.
