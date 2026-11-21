# createDiscount

`POST /stores/{storeId}/discounts`

4.1 '문구 만들기' (dryRun=true면 기대 절감만 계산)

## 공개 계약

- operationId: `createDiscount`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `dryRun` (query): {default: false, type: boolean} — 선택

요청 본문: `application/json`, 필수, `DiscountCreate`.

| 성공 상태 | 응답 |
|---|---|
| 200 | `Discount` |
| 201 | `Discount` |

## 책임과 경계

discounts / `CreateDiscount.execute(StoreContext, DiscountInput, dryRun:bool) → DiscountEvaluation`. dryRun과 실제 생성이 같은 `DiscountCalculator`를 쓴다. 계산 규칙은 [C06](../domain/calculations.md)이다.

## 처리 순서

1. store/menu 소속을 확인한다(아니면 404). 메뉴 판매가가 없으면 422. 오늘이 휴무면 422. quantity≥1, 할인 값 범위, HH:mm을 검사한다. startTime은 오늘 영업 구간 `[시작, 종료)` 안이어야 한다(심야 영업은 C01). 요청 시각보다 이른 startTime도 받는다.
2. recommendationId가 있으면 `rec_<오늘>_<요청 menuId>`와 같은지 확인한다. 다르면 422. 할인 폭·시각·수량은 요청값으로 계산한다.
3. 판매가, 레시피, 최근 단가로 할인가·revenueReduction·wasteCostAvoided를 계산한다. 재료 원가를 계산할 수 없으면 `wasteCostAvoided`를 생략한다(D33). 그래도 만들 수 있다.
4. `promoText`는 [C07](../domain/calculations.md) 템플릿, `channels`는 정책 파일 순서다. `date`는 오늘이다.
5. dryRun=true: 읽기 전용으로 계산해 id=`preview_`+입력 해시로 200. DB에 쓰지 않고 작업도 등록하지 않는다.
6. dryRun=false: store를 잠그고 다시 계산해 discount 한 행(menu_id, discount_type, discount_value, start_time, quantity, original_price, waste_cost_avoided, revenue_reduction, promo_text)을 저장하고 201.

응답의 `discountedPrice`는 저장하지 않고 `original_price`와 할인 값으로 계산한다. `menu`와 `unit`은 현재 메뉴 값이다.

## 원자성·실패

없는 menu는 404, 할인 범위·판매가 없음·휴무·recommendationId 불일치는 422, DB 오류는 500. preview와 생성 사이에 판매가나 단가가 바뀌면 값이 달라질 수 있다. 같은 데이터에서는 id만 다르고 계산값이 같다. POST를 다시 보내면 새 할인이 생긴다.

## 완료 조건

PERCENT 0/100/100 초과, AMOUNT 소수·판매가 초과, quantity 0/1, 판매가 없음, 원 단위 반올림, 재료 원가를 모를 때 필드 생략, dryRun이 쓰지 않고 목록에도 없는 것, 생성 뒤 이력과 일치, 재고가 바뀌지 않는 것을 검증한다.
