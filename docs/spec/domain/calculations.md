# 도메인 계산 규칙

수량 단위는 두 종류뿐이다. 메뉴 수량은 모두 `인분`이다(D46). 재료 수량은 그 재료의 단위인 `개` 또는 `g`이고, 레시피 사용량·입고량·재고·발주 수량이 모두 이 단위다(D47). 판·봉 같은 구매 단위와 환산 비율은 없다.

모든 계산은 불변 입력 스냅샷, [정책 파일](../../../app/policies/defaults.toml) 값, Clock에서 받은 기준 시각으로 결정된다. 소수는 Decimal이고 중간 계산은 반올림하지 않는다. 정책 파일의 숫자는 AI 초안이며 예측 성능을 보장하지 않는다. 아래에서 `policy.x.y`는 정책 파일의 `[x] y`다.

## C01 영업일과 홈

`localNow`는 기준 시각을 Asia/Seoul로 바꾼 값이다. 시각이 `policy.home.tomorrow_start`(20:00) 미만이면 view=TODAY, targetDate=오늘. 그 이상이면 view=TOMORROW, targetDate=내일(D01). 휴무일을 건너뛰지 않는다. targetDate가 휴무면 예측 items=[], 발주 요약 null이다.

DayHours는 MON~SUN 정확히 1개씩이다. closed=false면 open/close 필수, open=close는 422. open>close는 익일 마감 영업이다. 영업일은 문을 여는 날짜에 속한다.

## C02 예측

**판매 기록이 있는 날**은 반영이 끝난(COMPLETED) 판매 파일의 기간(`period_from~period_to`) 안에 든 날이다. 첫 파일에서 기간 안에 행이 없는 메뉴는 0개 판매다(D08). 새 파일에서 빠진 기존 판매 칸의 처리는 U02 결정 대상이다. **학습일**은 판매 기록이 있는 날 중 현재 영업시간표에서 그 요일이 휴무가 아닌 날이다.

예측은 과거 판매 엑셀에서 구한 가게별 예측 모델(`store.forecast_model`)을 쓴다(D39). 모델은 세 가지 값이다.

- `weekdayIndex[요일]`: 이 가게가 그 요일에 평소보다 얼마나 팔리는지(가게 단위).
- `weatherIndex[메뉴][날씨 구간]`: 그 메뉴가 그 기온·강수 조건에서 평소보다 얼마나 팔리는지(메뉴 단위).
- `weights.weekday`, `weights.weather`: 두 경향을 얼마나 반영할지 정하는 0~1 사이 가중치.

행사는 과거 판매에서 구하지 않고 정책 파일의 고정 계수를 쓴다(D10). 날씨에는 고정 계수가 없다. 그 가게의 판매 기록에서 구한 경향만 쓴다.

### 경향 계산

```text
storeTotal(d)         = Σ_m sales_m(d)
weekdayIndex[w]       = 요일이 w인 학습일의 storeTotal 평균 ÷ 전체 학습일의 storeTotal 평균
                        (요일 w의 학습일이 policy.forecast.min_weekday_days 미만이거나 전체 평균이 0이면 1.0)
menuMean_m            = 전체 학습일의 sales_m 평균
weatherIndex[m][구간]  = Σ sales_m(d) ÷ Σ (menuMean_m × weekdayIndex[요일(d)])     (그 구간에 든 학습일만 더한다)
                        (그 구간의 학습일이 policy.forecast.min_bucket_days 미만이거나 분모가 0이면 값을 만들지 않는다)
                        (값은 policy.forecast.index_min 이상 index_max 이하로 자른다)
```

날씨 구간은 `(기온 구간, 강수 여부)`다. 기온 구간은 `policy.forecast.temperature_bands`의 경계(℃)로 나눈다. 경계가 `[0, 10, 20, 28]`이면 0 미만, 0 이상 10 미만, 10 이상 20 미만, 20 이상 28 미만, 28 이상의 5구간이다.

| 날짜 | 기온 | 강수 여부 |
|---|---|---|
| 과거(경향 계산) | `weather_observation.max_temperature_c` | `precipitation_mm`이 `policy.forecast.wet_min_mm` 이상이면 강수 |
| 대상일(예측) | `weather.temperature_c` | `weather.condition`이 RAIN, SNOW, RAIN_SNOW, SHOWER면 강수 |

관측 행이 없는 학습일은 날씨 경향 계산에서 뺀다. 요일 경향에는 그대로 쓴다.

### 추천 준비량

메뉴 m, 대상일 d(요일 w). 휴무일인 d는 예측을 만들지 않는다.

```text
weekdayFactor(w) = 1 + weights.weekday × (weekdayIndex[w] − 1)
level            = Σ sales_m(d') ÷ Σ weekdayFactor(요일(d'))
                   (d'은 d 이전 28일 안의 학습일. 학습일이 없거나 분모가 0이면 그 메뉴의 예측을 만들지 않는다)
baseline         = level × weekdayFactor(w)
weatherFactor    = weatherIndex[m][대상일 구간]이 있으면 1 + weights.weather × (그 값 − 1)
                   없거나 날씨를 모르면 1.0   (그 날씨 구간의 판매 기록이 모자라면 보정하지 않는다)
eventFactor      = 반경 policy.forecast.event_radius_meters 안에 d의 영업시간과 겹치는 행사(D29의 분류만 저장됨)가 하나라도 있으면
                   policy.forecast.event_factor, 없으면 1.0   (행사가 여러 개여도 한 번만 곱한다)
recommended      = ceil(baseline × weatherFactor × eventFactor)
eventDiff        = recommended − ceil(baseline × weatherFactor)
planned          = adjusted가 null이 아니면 adjusted, 아니면 recommended
comparison.diff      = roundHalfUp(recommended − baseline)      (정수)
comparison.direction = |diff| ≤ policy.forecast.similar_max_diff 이면 SIMILAR, diff>0 MORE, diff<0 LESS
```

`level`은 요일 영향을 뺀 최근 판매 수준이고, `baseline`은 거기에 대상 요일의 경향을 다시 곱한 값이다. 화면의 "평소"는 baseline이다.

모델이 아직 없는 가게(`store.forecast_model`이 null)는 `weekdayIndex` 전부 1.0, `weatherIndex` 값 없음으로 계산한다. 이 경우 baseline은 최근 28일 학습일의 평균이고 날씨 보정은 없다.

comparison은 사용자가 고친 값(adjusted)이 아니라 recommended 기준이다. `adjusted=0`과 `produced=0`은 null과 다르며 예측을 다시 만들어도 그대로 둔다.

### 가중치 조절 (D40)

가중치는 가게마다 서버가 정한다. 판매 반영이 끝날 때마다 FIT_FORECAST_MODEL 작업이 그 가게의 학습일 전체로 다시 계산해 모델을 덮어쓴다.

1. 학습일을 날짜순으로 놓는다. 학습일 수가 `policy.forecast.fit_min_days` 미만이면 조절하지 않고 두 가중치를 1.0으로 둔다(4번으로 간다).
2. 앞쪽 절반(`floor(학습일 수 ÷ 2)`일)만으로 `weekdayIndex`, `weatherIndex`를 계산한다.
3. 나머지 뒤쪽 학습일의 메뉴·날짜 칸마다 `예측값 = level × weekdayFactor × weatherFactor`를 계산해 실제 판매량과 비교한다. `level`은 실제 예측과 같은 방식(그 날짜 이전 28일의 실제 판매)으로 구한다. 올림하지 않고 행사 계수를 곱하지 않는다. level을 만들 수 없는 칸은 뺀다.
   - `오차 = Σ|예측값 − 실제 판매량| ÷ Σ 실제 판매량`
   - 가중치 후보는 `0, 0.25, 0.5, 0.75, 1` 다섯 값이다. `(weekday, weather)` 조합 25개 전부에 대해 오차를 구해 가장 작은 조합을 고른다. 오차가 같으면 weekday가 큰 조합, 그다음 weather가 큰 조합을 고른다. 실제 판매량 합이 0이면 두 가중치를 1.0으로 둔다.
4. 고른 가중치와, **전체 학습일**로 다시 계산한 `weekdayIndex`·`weatherIndex`를 모델에 저장한다. `holdoutError`에는 3번의 오차(조절하지 않았으면 null), `tunedDays`에는 학습일 수, `fittedAt`에는 계산 시각을 넣는다.

같은 판매·관측 자료로 다시 실행하면 같은 모델이 나온다. 과거 행사 자료는 쓰지 않으므로 행사가 있던 날의 판매 증가는 요일·날씨 경향에 섞여 들어간다.

### 밤사이 변경과 홈 목록

**밤사이 변경**은 같은 대상일의 NIGHT와 MORNING 결과를 비교한다. NIGHT 성공 때 메뉴별 recommended와 당시 날씨 문구를 해당 작업의 내부 결과에 남긴다. MORNING은 그 밤의 값과 추천량이 달라진 메뉴에만 밤 값을 `previous_recommended`로 기록한다. 밤 기준값이 없거나 추천량이 같으면 null이다. 날씨 문구도 두 slot의 값이 모두 있고 다를 때만 changeText를 만든다. DATA_CHANGED는 밤 기준값을 만들지 않는다. 낮 재계산이 recommended를 변경하면 해당 메뉴의 이전 비교 표시는 지운다. 재시도는 같은 밤 기준값으로 비교한다.

홈 상위 3개는 대상일 이전 28일 판매합 내림차순, 동률은 menuId 순이다. `totalMenuCount`는 등록된 전체 메뉴 수다. 예측이 있는 메뉴가 2개면 2개만 보낸다.

## C03 단가와 현재 거래처

재료의 확정 receipt_line 중 `(purchased_on DESC, id DESC)` 첫 줄 하나에서 거래처·품명·단가를 함께 쓴다. 단가는 재료 단위 1개(1개 또는 1g)당 원이고 `영수증 줄 금액 ÷ 입고량`이다(소수 6자리). 그 줄의 단가가 null이면 더 오래된 줄의 단가를 끌어오지 않는다. 최근 거래처가 오늘 주문 요일이 아니라고 예전 거래처로 바꾸지 않는다.

## C04 재고 추정

재고는 `입고 − POS 판매량 × 레시피`다. 입고는 확정한 영수증과 사장님이 직접 넣은 수기 입고이고 같은 장부(`receipt_line`)에 있다(D53). 차감은 판매 기록뿐이다(D03). 발주 생성·전송 기록·준비량·만든 양은 입출고에 넣지 않는다. 모든 양은 재료 단위(개 또는 g)다.

`inventory.evaluate(..., asOf)`는 **asOf 날짜의 영업이 끝났을 때 남는 양**을 재료별로 계산하는 순수 함수다. 입고가 하나도 없는 재료는 `stock=null`이다.

```text
장부      = asOf 이전(포함) 입고를 (purchased_on, id) 순으로 놓고, '현재 재고로 설정' 줄이 있으면 가장 최근 그 줄부터 (D54)
시작일    = 장부 첫 줄의 purchased_on (그 이전 재고는 0)
usage     = Σ_{시작일 ≤ d ≤ asOf} Σ_m 판매량_m(d) × 레시피 사용량_m
stock     = Σ 장부.quantity − usage
```

1. 판매 기록이 없는 날은 사용량이 0이다. 준비량이나 만든 양으로 추정하지 않는다.
2. 결과는 0으로 자르지 않는다. 음수면 입고 기록이 빠졌을 수 있다는 경고를 함께 준다(D55). 발주(C05)와 재고 부족 안내(C06)는 음수 재고를 0으로 본다.
3. 시작일 이전 판매는 차감하지 않는다.

판매 엑셀을 올리지 않은 기간에는 재고가 줄지 않는다. 그 기간의 발주 수량(C05)과 재고 부족 안내(C06)는 실제보다 재고를 많게 본 값이며, 엑셀이 반영되면 다음 조회부터 바로잡힌다.

레시피는 현재 레시피를 과거에도 적용한다. 레시피가 바뀌면 다음 조회부터 결과가 달라진다. 이 값은 추정이며 실제 재고 실사가 아니다. 장부가 틀어지면 수기 입고의 '현재 재고로 설정'으로 다시 맞춘다.

## C05 발주

발주 계획은 `ordering.PlanOrder(targetDate)` 한 곳에서 만든다. 수량은 모두 재료 단위(개 또는 g)다.

1. targetDate의 최종 준비량과 레시피로 `need_i = Σ(planned_m × recipeUsage_mi)`.
2. 재고는 `asOf = 오늘`(요청 시각의 KST 날짜)로 계산하고 음수면 0으로 본다(C04, D56).
3. `shortage = max(0, need − stock)`.
4. `quantity = ceil(shortage)`. 0이면 줄을 뺀다. 포장 단위로 맞춰 올리지 않는다. 사장님이 2.8에서 +/−로 고친 수량이 발주서에 들어간다.
5. 최근 거래처(C03)가 있는 재료는 요청 시각의 요일이 주문 요일이고 `orderDaysStatus`가 ESTIMATED 또는 CONFIRMED인 거래처일 때만 넣는다. UNKNOWN이거나 요일이 비었으면 뺀다. 영수증 입고가 없어 거래처를 모르는 재료는 `supplier=null`인 '거래처 미정' 묶음에 넣는다(D56). 이 줄은 단가가 null이고 발주서를 만들 수 없다.
6. 각 줄은 quantity, stock, eventDiff를 같은 재료 단위로 가진다. `price.unitPrice`는 null일 수 있다.

**eventDiff**: 같은 조건에서 `eventFactor=1`로 준비량을 다시 계산해 만든 계획의 quantity와의 차이다. 사용자가 고친 메뉴(adjusted)는 행사 유무와 무관하게 같은 값을 쓰므로 그 메뉴의 증분은 0이다. `OrderPlan.events`에는 eventFactor가 1이 아닐 때 그 근거가 된 행사를 넣고, 아니면 빈 배열이다.

**금액(D13)**: 합계는 단가를 아는 줄의 `roundHalfUp(quantity × unitPrice)`만 더한다. 단가를 아는 줄이 하나도 없으면 null이다. `Home.order.estimatedTotal`, `OrderPlan.usualTotal`, `purchase_order.total_amount`가 모두 이 규칙을 쓴다. `usualTotal`은 준비량을 baseline(올림)으로 바꿔 같은 방법으로 계산한 계획의 합계다. 줄이 하나도 없으면 `suppliers=[]`이고 `Home.order=null`이다(D20).

사례: 김밥 1인분당 계란 1개, 계획 75인분, 재고 15개면 부족 60개 → 발주 60개. 행사 없는 준비량이 60인분이면 부족 45개이므로 eventDiff=15개. 계란 30개를 8,900원에 샀으면 단가는 296.666667원이고 60개는 17,800원이다. 떡 재고 1,500g, 필요 6,037.5g이면 발주 4,538g이다.

발주서 생성은 추천을 다시 계산해 요청을 덮지 않는다. 요청 수량이 최종이다. 모든 줄이 0이거나 줄이 없는 거래처는 422. 0인 줄은 뺀다.

## C06 재고 부족 안내

재료의 남은 재고가 앞으로 `policy.low_stock.days`일 동안 쓸 것으로 예상되는 양보다 적으면 사장님에게 발주를 안내한다(D41, D52). 저장하지 않고 조회할 때 계산한다. `ordering.FindLowStock(targetDate)` 한 곳에서 만든다.

```text
days            = targetDate부터 날짜순으로 휴무가 아닌 날 policy.low_stock.days 개
                  (targetDate를 포함해 14일 안에서 찾고, 모자라면 찾은 만큼만 쓴다)
quantity_m(d)   = d에 메뉴 m의 예측 행이 있으면 planned
                  없으면 C02에서 weatherFactor=1, eventFactor=1로 계산한 값의 올림
                  그 메뉴의 예측을 만들 수 없으면 0
expectedUsage_i = Σ_{d∈days} Σ_m quantity_m(d) × recipeUsage_mi
stock_i         = C04, asOf = 오늘(요청 시각의 KST 날짜)
lowStock_i      = stock_i가 null이 아니고, expectedUsage_i > 0 이고, stock_i < expectedUsage_i
ordered_i       = 재료 i가 줄에 든 발주서 중 sent_at이 있고, sent_at의 KST 날짜가 그 재료의 최근 입고일(C03의 purchased_on)보다 늦은 것이 있으면 true
```

targetDate는 C01의 값이다(20:00 전에는 오늘, 이후에는 내일). 재고는 오늘까지의 입고와 판매 기록을 반영한 값이라서 오늘 날짜 영수증을 확정하면 바로 반영된다. C05의 재고와 같은 기준일이다. `stock=null`인 재료는 부족 여부를 판단할 수 없으므로 안내하지 않는다. 목록은 `stock ÷ expectedUsage` 오름차순, 동률은 ingredientId 순이다.

`ordered`는 앱에서 발주서를 보낸 뒤 아직 그 재료의 영수증이 올라오지 않았다는 뜻이다. 앱 밖에서 한 발주는 알 수 없으므로 `ordered=false`인 재료에도 영수증 안내를 함께 적는다(C07).

사례: 김밥 1인분당 계란 1개, 오늘과 내일 준비량이 60인분씩이면 expectedUsage는 120개다. 재고 100개면 부족, 120개면 부족이 아니다. 내일이 휴무이고 모레 준비량이 50인분이면 expectedUsage는 110개다.

이 안내는 홈 배너와 NIGHT 푸시에만 쓴다. 재고를 바꾸거나 발주서를 만들지 않는다.

## C07 배너·서버 문장

### 표시 조건

- FIRST_FORECAST: `store.first_forecast_at`의 KST 날짜가 오늘이고 예측 items가 1개 이상일 때. 닫힘은 프론트가 기억한다.
- OVERNIGHT_CHANGE: view=TODAY이고 `previousRecommended`가 있는 메뉴가 있거나 `weather.changeText`가 있을 때.
- EVENT_TODAY: view=TODAY이고 오늘 영업시간과 겹치는 반경 내 행사가 있을 때.
- DELIVERY_EXPECTED: view=TODAY이고 `target_date=오늘`이며 `sent_at`이 있는 발주서가 있을 때. 입고 예정 안내일 뿐 입고가 아니다.
- LOW_STOCK: C06의 부족 목록이 1개 이상일 때. view와 무관하다.

### 문장 템플릿

서버가 만드는 문장은 아래 템플릿만 쓴다. LLM으로 문장을 만들지 않는다. 메뉴 수량 뒤에는 항상 `인분`, 재료 수량 뒤에는 그 재료의 단위(`개` 또는 `g`)를 붙인다. 수량에는 천 단위 쉼표를 넣지 않는다. 조사(이/가, 을/를)는 앞 단어의 받침 유무로 고른다. 시각은 `HH:mm` 또는 `H시`, 날짜는 `M/D`, 금액은 천 단위 쉼표다.

| 필드 | 템플릿 | 예 |
|---|---|---|
| `WeatherSummary.text` | `{상태}` · 낮에 비/눈이 시작하면 `{상태} · {H}시부터` | 비 · 14시부터 |
| `WeatherSummary.changeText` | 아침 갱신 때 저장돼 있던 전날 밤 `text`와 새 `text`가 다르면 `{전날 밤 text} → {새 text}`, 같으면 null | 비 · 14시부터 → 비 · 13시부터 |
| `ForecastItem.summaryText` | `평소 {요일}요일 {baseline 반올림}인분` + 행사 계수가 1이 아니면 ` · 행사 +{p}%` + 날씨 계수가 1이 아니면 ` · {상태} {±p}%` (p는 계수에서 1을 뺀 값의 백분율 반올림) | 평소 토요일 58인분 · 행사 +20% |
| FIRST_FORECAST | title `첫 예측이 준비됐어요` · message `{n}주치 판매 기록으로 계산했어요` (n은 판매 기록 날짜 수 ÷ 7 올림, D34) | 6주치 판매 기록으로 계산했어요 |
| OVERNIGHT_CHANGE | 추천량이 바뀐 메뉴가 있으면 title `밤사이 예보가 바뀌어 {메뉴}{을/를} {n}인분 {줄였어요\|늘렸어요}` (차이가 가장 큰 메뉴 1개) · 날씨만 바뀌었으면 title `밤사이 날씨 예보가 바뀌었어요` · message null | 밤사이 예보가 바뀌어 김밥을 2인분 줄였어요 |
| EVENT_TODAY | title `오늘 근처 행사 · {시작} ~ {끝}` (가장 가까운 행사. 시각이 없는 자료의 표시는 U03) · 붐비는 시간을 알면 message `{H}~{H}시가 가장 붐벼요 · {행사 시작 H}시 전에 {상위 1개 메뉴} 준비를 끝내두세요`, 모르면 null | 13~16시가 가장 붐벼요 · 12시 전에 김밥 준비를 끝내두세요 |
| DELIVERY_EXPECTED | title `어젯밤 발주한 재료가 오전에 들어와요` · message `{재료} {수량}{재료 단위}`를 ` · `로 이어 최대 3개, 더 있으면 ` 외 {n}개` | 계란 90개 · 김 200개 · 떡 5000g |
| LOW_STOCK | title `{재료 이름} 재고가 얼마 안 남았어요` (부족 목록 순서로 최대 2개를 `·`로 잇고, 더 있으면 ` 외 {n}개`) · message 목록의 재료가 전부 `ordered=true`면 `이미 발주한 재료예요 · 받았다면 영수증을 올려주세요`, 아니면 `발주해 주세요 · 이미 발주했다면 영수증을 올려주세요` | 계란·김 외 1개 재고가 얼마 안 남았어요 |
| `PurchaseOrder.supplier.phoneSourceText` | 영수증에서 읽은 미확인 번호면 `{M/D} 영수증에서 읽었어요. 다음부터는 묻지 않아요`, 그 외 null | 11/8 영수증에서 읽었어요. 다음부터는 묻지 않아요 |
| `PurchaseOrder.messageText` | 1줄 `[{가게명}] {targetDate M/D}({요일}) 발주` · 줄마다 `{품명} × {수량}{재료 단위}` (예: `계란 특란 30구 × 60개`) · 끝줄 `{내일\|M/D} 오전 {targetDate 여는 시각 H}시 전 도착 부탁드립니다.` (targetDate가 요청일 다음 날이면 `내일`). 품명은 최근 영수증의 품명, 없으면 재료 이름 | [행복분식] 11/11(수) 발주 … |
| `ReceiptScan.removedDuplicateTexts` | 중복: `{M/D} {거래처} 영수증이 두 번 찍혀서 1장은 뺐어요` · 못 읽은 사진: `{n}번째 사진은 읽지 못해서 뺐어요` · 못 읽은 줄(개·g으로 환산하지 못했거나 재료의 단위와 달라 뺀 줄 포함, D31): `{M/D} {거래처} 영수증에서 {n}줄은 읽지 못해서 뺐어요` | 11/8 대한식자재 영수증이 두 번 찍혀서 1장은 뺐어요 |
| 푸시 NIGHT | title `내일 준비량이 나왔어요` · body `{상위 1개 메뉴} {planned}인분 등 {N}개 메뉴` + C06 부족 목록 중 `ordered=false`인 재료가 있으면 ` · {첫 재료} 발주가 필요해요`, 2개 이상이면 ` · {첫 재료} 외 {n}개 발주가 필요해요` | 김밥 60인분 등 12개 메뉴 · 계란 외 1개 발주가 필요해요 |
| 푸시 MORNING | title `오늘 미리 만들어둘 양` · body `{상위 1개 메뉴} {planned}인분 등 {N}개 메뉴` (재고 부족 문구를 붙이지 않는다) | 김밥 48인분 등 12개 메뉴 |

날씨 계수는 대상일의 기온 구간과 강수 여부로만 정한다. 같은 상태에서 비 시작 시각만 바뀐 경우 `changeText`는 바뀌지만 추천량은 바뀌지 않는다. ERD의 weather에 시간대별 값이 없기 때문이다.
