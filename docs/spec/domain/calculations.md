# 도메인 계산 규칙

모든 계산은 불변 입력 스냅샷, [정책 파일](../../../app/policies/defaults.toml) 값, Clock에서 받은 기준 시각으로 결정된다. 소수는 Decimal이고 중간 계산은 반올림하지 않는다. 정책 파일의 숫자는 AI 초안이며 예측 성능을 보장하지 않는다. 아래에서 `policy.x.y`는 정책 파일의 `[x] y`다.

## C01 영업일과 홈

`localNow`는 기준 시각을 Asia/Seoul로 바꾼 값이다. 시각이 `policy.home.tomorrow_start`(20:00) 미만이면 view=TODAY, targetDate=오늘. 그 이상이면 view=TOMORROW, targetDate=내일(D01). 휴무일을 건너뛰지 않는다. targetDate가 휴무면 예측 items=[], 발주 요약 null이다.

DayHours는 MON~SUN 정확히 1개씩이다. closed=false면 open/close 필수, open=close는 422. open>close는 익일 마감 영업이다. 영업일은 문을 여는 날짜에 속한다.

## C02 예측

**판매 기록이 있는 날**은 반영이 끝난(COMPLETED) 판매 파일의 기간(`period_from~period_to`) 안에 든 날이다. 첫 파일에서 기간 안에 행이 없는 메뉴는 0개 판매다(D08). 새 파일에서 빠진 기존 판매 칸의 처리는 U02 결정 대상이다.

메뉴 m, 대상일 d의 baseline은 `d−7, d−14, d−21, d−28` 중 판매 기록이 있는 날의 판매량 평균이다. 기록이 없는 날은 표본에서 뺀다. 표본이 0개면 그 메뉴의 예측을 만들지 않는다. 휴무일인 d는 예측을 만들지 않는다.

```text
weatherFactor = policy.forecast.weather_factor_by_category[업종][상태]
                없으면 policy.forecast.weather_factor[상태], 그것도 없거나 날씨를 모르면 1.0
eventFactor   = 반경 policy.forecast.event_radius_meters 안에 d의 영업시간과 겹치는 행사(D29의 분류만 저장됨)가 하나라도 있으면
                policy.forecast.event_factor, 없으면 1.0   (행사가 여러 개여도 한 번만 곱한다)
recommended   = ceil(baseline × weatherFactor × eventFactor)
eventDiff     = recommended − ceil(baseline × weatherFactor)
planned       = adjusted가 null이 아니면 adjusted, 아니면 recommended
comparison.diff      = roundHalfUp(recommended − baseline)      (정수)
comparison.direction = |diff| ≤ policy.forecast.similar_max_diff 이면 SIMILAR, diff>0 MORE, diff<0 LESS
```

comparison은 사용자가 고친 값(adjusted)이 아니라 recommended 기준이다. `adjusted=0`과 `produced=0`은 null과 다르며 예측을 다시 만들어도 그대로 둔다.

**밤사이 변경**은 같은 대상일의 NIGHT와 MORNING 결과를 비교한다. NIGHT 성공 때 메뉴별 recommended와 당시 날씨 문구를 해당 작업의 내부 결과에 남긴다. MORNING은 그 밤의 값과 추천량이 달라진 메뉴에만 밤 값을 `previous_recommended`로 기록한다. 밤 기준값이 없거나 추천량이 같으면 null이다. 날씨 문구도 두 slot의 값이 모두 있고 다를 때만 changeText를 만든다. DATA_CHANGED는 밤 기준값을 만들지 않는다. 낮 재계산이 recommended를 변경하면 해당 메뉴의 이전 비교 표시는 지운다. 재시도는 같은 밤 기준값으로 비교한다.

홈 상위 3개는 대상일 이전 28일 판매합 내림차순, 동률은 menuId 순이다. `totalMenuCount`는 등록된 전체 메뉴 수다. 예측이 있는 메뉴가 2개면 2개만 보낸다.

## C03 단가와 현재 거래처

재료의 확정 receipt_line 중 `(purchased_on DESC, id DESC)` 첫 줄 하나에서 거래처·품명·단가를 함께 쓴다. 그 줄의 단가가 null이면 더 오래된 줄의 단가를 끌어오지 않는다. 최근 거래처가 오늘 주문 요일이 아니라고 예전 거래처로 바꾸지 않는다.

단가 변동 알림(PRICE_CHANGED)은 같은 재료의 최근 두 유효 단가를 비교한다. 이전 단가>0이고 `|new−old|/old ≥ policy.price.change_alert_ratio`일 때 만든다. 알림 ID는 `price:<ingredientId>:<latestLineId>`다.

## C04 재고 추정

입고는 확정한 영수증뿐이다(D02). 발주 생성·전송 기록·할인 설정은 입출고에 넣지 않는다.

`InventoryCalculator.evaluate(..., asOf)`는 **asOf 날짜의 영업이 끝났을 때 남는 양**을 재료별로 계산한다. 환산 비율(1판=30개)이 없거나 확정한 입고가 하나도 없는 재료는 `stock=null`이다.

1. 시작일은 그 재료의 가장 이른 입고일이고 시작 재고는 0이다.
2. 시작일부터 asOf까지 하루씩 진행한다. 하루 시작에 그날 입고(`구매 수량 × usagePerPurchase`)를 lot으로 더한다.
3. 하루 끝에 그날 사용량을 오래된 lot부터 뺀다(FIFO). 사용량은 `Σ(메뉴별 그날 수량 × 현재 레시피의 재료 사용량)`이고, 메뉴별 그날 수량은 위에서부터 먼저 해당하는 것을 쓴다(D03).
   1. 휴무일이면 0.
   2. 판매 기록이 있는 날이면 판매량.
   3. 그날 예측 행이 있으면 만든 양(`produced`), 만든 양이 null이면 최종 준비량(`planned`). 오늘도 이 규칙을 쓴다.
   4. 그날 기준 지난 4주 같은 요일 판매 평균(C02의 baseline과 같은 방식). 표본이 없으면 그 재료는 `stock=null`.
4. 사용량이 남은 재고보다 많으면 재고를 0으로 두고 계속한다. 모자란 만큼은 영수증에 없는 재고를 쓴 것으로 본다.
5. 추정 기한은 `purchasedOn + shelf_life_days − 1일`이다. 기한이 지난 lot은 그 다음 날 시작에 재고에서 뺀다. `shelf_life_days`가 null이면 기한이 없는 lot이다.
6. `stock`은 남은 lot의 합(사용 단위)이다. 구매 단위 표시는 `usagePerPurchase`로 나눈 값이다.

레시피는 현재 레시피를 과거에도 적용한다. 레시피가 바뀌면 다음 조회부터 결과가 달라진다. 이 값은 추정이며 실제 재고 실사가 아니다.

## C05 발주

발주 계획은 `ordering.PlanOrder(targetDate)` 한 곳에서 만든다.

1. targetDate의 최종 준비량과 **CORE** 재료 레시피로 `needUsage_i = Σ(planned_m × recipeUsage_mi)`.
2. 재고는 `asOf = targetDate − 1일`로 계산한다(C04). 남은 lot 중 추정 기한이 targetDate 이상인 것만 `stockUsable`로 더한다. `stock=null`이면 차감 0으로 계산하고 응답 stock도 null이다. 계산된 0과 구분한다.
3. `shortageUsage = max(0, needUsage − stockUsable)`.
4. `orderPacks = ceil(shortageUsage / usagePerPurchase)`. 0이면 줄을 뺀다.
5. 최근 거래처(C03)와 환산 비율이 있는 줄만 넣는다. 요청 시각의 요일이 주문 요일이고 `orderDaysStatus`가 ESTIMATED 또는 CONFIRMED인 거래처만 보여준다. UNKNOWN이거나 요일이 비었으면 뺀다.
6. 각 줄은 구매 단위 quantity, 같은 단위 stock, 같은 단위 eventDiff를 가진다. `price.unitPrice`는 null일 수 있다.

**할인 화면의 발주 감소 판정**도 PlanOrder에서 구한다. 내일 주문 가능한 각 재료에 대해 C06에 표시할 기간(내일~`policy.discount.surplus_within_days`일)에 만료되는 잔여 lot만 제외했을 때와 포함했을 때의 팩 수를 같은 C05 식으로 비교한다. 나머지 재고와 준비량·거래처·요일은 고정한다. 포함 후 팩 수가 실제로 줄어든 재료 ID를 내부 결과에 담는다. 1판에서 0판이 되어 공개 줄이 사라지는 경우도 포함한다. 휴무·주문 불가·환산 불가인 재료는 제외한다.

**eventDiff**: 같은 조건에서 `eventFactor=1`로 준비량을 다시 계산해 만든 계획의 팩 수와의 차이다. 사용자가 고친 메뉴(adjusted)는 행사 유무와 무관하게 같은 값을 쓰므로 그 메뉴의 증분은 0이다. `OrderPlan.events`에는 eventFactor가 1이 아닐 때 그 근거가 된 행사를 넣고, 아니면 빈 배열이다.

**금액(D13)**: 합계는 단가를 아는 줄의 `roundHalfUp(quantity × unitPrice)`만 더한다. 단가를 아는 줄이 하나도 없으면 null이다. `Home.order.estimatedTotal`, `OrderPlan.usualTotal`, `purchase_order.total_amount`가 모두 이 규칙을 쓴다. `usualTotal`은 준비량을 baseline(올림)으로 바꿔 같은 방법으로 계산한 계획의 합계다. 줄이 하나도 없으면 `suppliers=[]`이고 `Home.order=null`이다(D20).

사례: 계란 1개/김밥, 계획 75줄, 쓸 수 있는 재고 15개, 1판=30개면 부족 60개 → 2판. 행사 없는 준비량이 60줄이면 부족 45개 → 2판이므로 eventDiff=0판. 재고가 null이면 75개 전체 → 3판. 1판=8,900원이면 2판은 17,800원.

발주서 생성은 추천을 다시 계산해 요청을 덮지 않는다. 요청 수량이 최종이다. 모든 줄이 0이거나 줄이 없는 거래처는 422. 0인 줄은 뺀다.

## C06 할인 추천과 기대 절감

할인 추천은 저장하지 않고 조회할 때 계산한다(D25). 요청 시각이 `policy.discount.base_time`(17:00) 전이거나 오늘이 휴무면 `recommendations=[]`, `surplusStock=[]`다. 재고는 `asOf = 오늘`로 계산한다(C04). 즉 오늘 영업이 끝났을 때 남을 것으로 보이는 양이다.

**남을 재고(surplusStock)**: 재고가 계산되는 재료만, ingredientId 순.

| 조건 | quantity | wasteCost | mitigation |
|---|---|---|---|
| 추정 기한이 오늘인 남은 lot이 있음 | 그 lot들의 남은 양(사용 단위) | `남은 양 × 단가 ÷ usagePerPurchase`를 원 단위 반올림. 단가를 모르면 null | 최종 선택된 추천이 이 재료의 오늘 만료 lot을 소진하면 DISCOUNT_RECOMMENDED, 아니면 NONE |
| 추정 기한이 내일부터 `policy.discount.surplus_within_days`일 안인 남은 lot이 있음 | 그 lot들의 남은 양 | null | 내일 PlanOrder의 감소 재료 ID에 포함되면 ORDER_REDUCED, 아니면 NONE |

한 재료가 두 조건에 모두 해당하면 첫째 줄만 만든다.

**추천(recommendations)**: 메뉴별로 사용량이 양수인 레시피 항목만 계산에 쓴다. 그중 기한이 오늘인 잔여 lot을 쓰는 재료가 하나 이상이어야 한다. 판매가, 계산에 쓰는 모든 재료의 재고와 임박 재료의 단가가 있어야 한다.

```text
qAll    = floor(min_i(stock_i / recipeUsage_mi))                 (그 메뉴의 모든 재료, recipeUsage=0인 재료 제외)
qExpiry = max_{i∈임박}(floor(surplus_i / recipeUsage_mi))
quantity = min(qAll, qExpiry)            (1 미만이면 후보에서 뺀다)
discount = PERCENT policy.discount.recommended_percent
startTime = max(영업 시작, 영업일의 base_time, 영업 종료 − policy.discount.start_minutes_before_close 분)
wasteCostAvoided = quantity만큼 만들 때 소진되는 임박 lot 사용량의 원가 합
revenueReduction = roundHalfUp((originalPrice − discountedPrice) × quantity)
expectedSavings  = wasteCostAvoided − revenueReduction    (양수인 후보만 남긴다)
```

시각은 C01의 영업 시작·종료 날짜를 붙여 비교한다. 시작 후보가 종료 이상이면 추천에서 뺀다. API에는 HH:mm으로 변환하며 dryRun·생성과 같은 영업 구간 `[시작, 종료)` 및 입력 검증을 쓴다.

후보 중 expectedSavings가 가장 큰 메뉴(동률은 menuId)를 고르고, 그 수량만큼 재고를 작업용 복사본에서 뺀 뒤 남은 후보를 다시 계산한다. 메뉴당 한 번만 고르고 양수 후보가 없으면 끝낸다. 같은 재고를 두 메뉴의 절감으로 두 번 세지 않는다. 추천 ID는 `rec_<YYYY-MM-DD>_<menuId>`다.

**사용자가 만드는 할인(dryRun 포함)**: recommendationId 없이도 만들 수 있다. 판매가를 모르면 422. PERCENT는 `0<value≤100`, AMOUNT는 원 정수 `0<value≤originalPrice`. `discountedPrice = roundHalfUp(originalPrice × (1 − percent/100))` 또는 `originalPrice − amount`. quantity≥1. 임박 재고나 단가를 몰라 버릴 원가를 계산할 수 없으면 `wasteCostAvoided`를 **생략**하고 `revenueReduction`만 돌려준다. 메뉴가 쓰는 모든 재료의 재고를 알고 임박 재고가 없으면 0이고, 재고를 모르는 재료가 있거나 레시피가 없으면 생략이다(D33). recommendationId가 오면 `rec_<오늘>_<요청 menuId>`와 같은지만 확인하고(다르면 422) 할인 폭·시각·수량은 요청값을 따른다. 현재 추천과 입력이 같으면 그 추천 순번에 배분된 재고와 같은 계산 결과를 사용한다. 다른 입력 또는 현재 목록에 없는 추천 ID는 수동 할인처럼 현재 재고에서 다시 계산한다. GET·dryRun·생성은 원가와 할인가 계산을 공유한다.

예: 판매가 5,000원, 10% 할인 5개 → 할인가 4,500원, revenueReduction 2,500원. 소진되는 임박 lot 원가가 9,000원이면 expectedSavings 6,500원.

기대 절감은 예상값이다. 실제 판매나 재고 차감으로 기록하지 않는다.

## C07 알림·배너·서버 문장

### 표시 조건

- FIRST_FORECAST: `store.first_forecast_at`의 KST 날짜가 오늘이고 예측 items가 1개 이상일 때. 닫힘은 프론트가 기억한다.
- OVERNIGHT_CHANGE: view=TODAY이고 `previousRecommended`가 있는 메뉴가 있거나 `weather.changeText`가 있을 때.
- EVENT_TODAY: view=TODAY이고 오늘 영업시간과 겹치는 반경 내 행사가 있을 때.
- DELIVERY_EXPECTED: view=TODAY이고 `target_date=오늘`이며 `sent_at`이 있는 발주서가 있을 때. 입고 예정 안내일 뿐 입고가 아니다.
- EXPIRY_IMMINENT: 추정 기한이 오늘인 남은 lot이 있는 재료마다 1개(C06의 재고 기준). 17시 전에는 만료 문구만 표시하고 `actions=[]`다(D28). 17시부터 같은 시각·데이터로 구한 할인 목록에서 그 재료가 `DISCOUNT_RECOMMENDED`일 때만 추천 문구와 `actions=[{type:OPEN_DISCOUNTS}]`를 붙인다.
- PRICE_CHANGED: C03.
- 할인 channels: `policy.discount.channels` 순서 그대로. `launch_url`이 빈 문자열이면 `launchUrl=null`.

### 문장 템플릿

서버가 만드는 문장은 아래 템플릿만 쓴다. LLM으로 문장을 만들지 않는다. 조사(이/가, 을/를)는 앞 단어의 받침 유무로 고른다. 시각은 `HH:mm` 또는 `H시`, 날짜는 `M/D`, 금액은 천 단위 쉼표다.

| 필드 | 템플릿 | 예 |
|---|---|---|
| `WeatherSummary.text` | `{상태}` · 낮에 비/눈이 시작하면 `{상태} · {H}시부터` | 비 · 14시부터 |
| `WeatherSummary.changeText` | 아침 갱신 때 저장돼 있던 전날 밤 `text`와 새 `text`가 다르면 `{전날 밤 text} → {새 text}`, 같으면 null | 비 · 14시부터 → 비 · 13시부터 |
| `ForecastItem.summaryText` | `지난 {n}주 {요일}요일 평균 {baseline 반올림}{단위}` (n은 평균에 쓴 표본 수, D34) + 행사 계수가 1이 아니면 ` · 행사 +{p}%` + 날씨 계수가 1이 아니면 ` · {상태} {±p}%` | 지난 4주 토요일 평균 58줄 · 행사 +20% |
| FIRST_FORECAST | title `첫 예측이 준비됐어요` · message `{n}주치 판매 기록으로 계산했어요` (n은 판매 기록 날짜 수 ÷ 7 올림, D34) | 6주치 판매 기록으로 계산했어요 |
| OVERNIGHT_CHANGE | 추천량이 바뀐 메뉴가 있으면 title `밤사이 예보가 바뀌어 {메뉴}{을/를} {n}{단위} {줄였어요\|늘렸어요}` (차이가 가장 큰 메뉴 1개) · 날씨만 바뀌었으면 title `밤사이 날씨 예보가 바뀌었어요` · message null | 밤사이 예보가 바뀌어 김밥을 2줄 줄였어요 |
| EVENT_TODAY | title `오늘 근처 행사 · {시작} ~ {끝}` (가장 가까운 행사. 시각이 없는 자료의 표시는 U03) · 붐비는 시간을 알면 message `{H}~{H}시가 가장 붐벼요 · {행사 시작 H}시 전에 {상위 1개 메뉴} 준비를 끝내두세요`, 모르면 null | 13~16시가 가장 붐벼요 · 12시 전에 김밥 준비를 끝내두세요 |
| DELIVERY_EXPECTED | title `어젯밤 발주한 재료가 오전에 들어와요` · message `{재료} {수량}{단위}`를 ` · `로 이어 최대 3개, 더 있으면 ` 외 {n}개` | 계란 3판 · 김 2속 · 떡 5kg |
| EXPIRY_IMMINENT | title `{재료} 오늘까지` + D28에 따라 이동 버튼을 표시할 때만 ` · 할인 추천이 있어요` · message `입고일 기준으로 계산한 예상 기한이에요` | 어묵 오늘까지 · 할인 추천이 있어요 |
| PRICE_CHANGED | title `{재료} 단가가 {올랐어요\|내렸어요}` · message `{재료} 단가가 {이전}원 → {현재}원으로 {올랐어요\|내렸어요} ({±p}%)` | 계란 단가가 7,900원 → 8,900원으로 올랐어요 (+13%) |
| `DiscountRecommendation.reasonText` | `{임박 재료}{이/가} 많이 들어가요` | 어묵이 많이 들어가요 |
| `Discount.promoText` | `갓 만든 {메뉴}, 마감 전에 할인해요` | 갓 만든 떡볶이, 마감 전에 할인해요 |
| `PurchaseOrder.supplier.phoneSourceText` | 영수증에서 읽은 미확인 번호면 `{M/D} 영수증에서 읽었어요. 다음부터는 묻지 않아요`, 그 외 null | 11/8 영수증에서 읽었어요. 다음부터는 묻지 않아요 |
| `PurchaseOrder.messageText` | 1줄 `[{가게명}] {targetDate M/D}({요일}) 발주` · 줄마다 `{품명} × {수량}{단위}` · 끝줄 `{내일\|M/D} 오전 {targetDate 여는 시각 H}시 전 도착 부탁드립니다.` (targetDate가 요청일 다음 날이면 `내일`). 품명은 최근 영수증의 품명, 없으면 재료 이름 | [행복분식] 11/11(수) 발주 … |
| `ReceiptScan.removedDuplicateTexts` | 중복: `{M/D} {거래처} 영수증이 두 번 찍혀서 1장은 뺐어요` · 못 읽은 사진: `{n}번째 사진은 읽지 못해서 뺐어요` · 못 읽은 줄(구매 단위가 달라 뺀 줄 포함, D31): `{M/D} {거래처} 영수증에서 {n}줄은 읽지 못해서 뺐어요` | 11/8 대한식자재 영수증이 두 번 찍혀서 1장은 뺐어요 |
| 푸시 NIGHT | title `내일 준비량이 나왔어요` · body `{상위 1개 메뉴} {planned}{단위} 등 {N}개 메뉴` | 김밥 60줄 등 12개 메뉴 |
| 푸시 MORNING | title `오늘 미리 만들어둘 양` · body 같은 형식 | 김밥 48줄 등 12개 메뉴 |

날씨 계수는 예보 상태(condition)로만 정한다. 같은 상태에서 비 시작 시각만 바뀐 경우 `changeText`는 바뀌지만 추천량은 바뀌지 않는다. ERD의 weather에 시간대별 값이 없기 때문이다.
