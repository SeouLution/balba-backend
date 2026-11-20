# 공통 타입과 모듈 경계

`T?`는 모름(null)을 뜻한다. `List<T>`는 순서가 고정된 불변 목록이다. 타입 표기는 구현 계약을 설명하기 위한 것이며 실행 코드가 아니다. 공개 DTO의 필드는 `openapi.yaml`을 그대로 따른다.

## 기본 값 타입

| 타입 | 표현 / 제약 | 책임 |
|---|---|---|
| StoreId, MenuId, IngredientId, SupplierId, ImportId, ScanId, PurchaseOrderId, DiscountId | 각각 별도 opaque string 타입 | 서로 바꿔 전달하지 않는다. 신규 ID는 prefix+UUID. |
| LocalDate | ISO 날짜 | 항상 KST 기준 영업일 |
| LocalTime | HH:mm | `24:00` 불가 |
| Instant | timezone-aware UTC datetime | DB timestamptz, 출력은 offset 포함 ISO 8601 |
| NonNegativeQty | finite Decimal ≥ 0, 소수 6자리 | 입력이 6자리를 넘으면 6자리로 반올림(D26). 저장은 numeric(18,6) |
| PositiveQty | finite Decimal > 0 | 환산 비율·입고량 |
| SignedQty | finite Decimal | diff·eventDiff |
| Won | int ≥ 0 | 가격/금액 |
| UnitLabel | nonempty string | 표시 단위. label이 같아도 다른 재료의 수량을 합치지 않는다. |
| UsageQuantity | `{ingredientId, value, unit}` | 재료 사용 단위 수량 |
| PurchaseQuantity | `{ingredientId, value, unit}` | 재료 구매 단위 수량 |
| Conversion | `{ingredientId, usageUnit, purchaseUnit, usagePerPurchase:PositiveQty}` | 구매→사용은 곱셈, 사용→구매는 나눗셈 |
| StoreContext | `{storeId, now:Instant, localDate:LocalDate}` | 요청 시작 때 Clock을 한 번 읽어 고정 |
| FileRef | `{key, sha256, mediaType, byteSize, originalName}` | 내부 저장 위치. 공개 URL이 아니다. |

공개 `Quantity`는 `{value, unit}`만 가진다. **사용 단위 수량과 구매 단위 수량을 맨 Decimal로 주고받지 않는다.**

금액은 원 단위에서 `ROUND_HALF_UP`으로 반올림한다. 구매 팩 수는 올림한다. 중간 계산은 반올림하지 않는다. Decimal이 JSON 문자열로 나가지 않게 number로 직렬화한다.

## 오류와 모름

- `UseCaseResult<T> = Success<T> | InvalidRequest(reason) | NotFound(kind) | InternalFailure(traceId)`. reason·traceId는 내부 전용이고 외부에는 422/404/500의 `{code}`만 나간다. 구현은 이 구분을 반환 타입 대신 예외로 표현한다(D36).
- 외부 포트는 `ProviderResult<T> = Available<T> | Unavailable(retryable, reason) | InvalidPayload(reason)`를 돌려준다. timeout과 "결과 0건"을 구분한다.
- 계산 결과가 모름일 수 있는 값은 `T?`로 표현한다. 모름을 0이나 빈 문자열로 바꾸지 않는다.

## 읽기 스냅샷

| 타입 | 필드 |
|---|---|
| StoreSnapshot | `id, name, category, roadAddress, businessHours:List<DayHours>, geoPoint:GeoPoint?, generationStatus, firstForecastAt:Instant?, createdAt` |
| MenuSnapshot | `id, storeId, name, unit, price:Won?` |
| IngredientSnapshot | `id, storeId, name, status:CORE\|LATER, usageUnit:UnitLabel?, purchaseUnitLabel:UnitLabel?, conversion:Conversion?, shelfLifeDays:int?, tags:List<Tag>` |
| RecipeSnapshot | `menu:MenuSnapshot, items:List<{ingredient:IngredientSnapshot, usagePerMenu:NonNegativeQty}>` |
| CatalogSnapshot | `storeId, menus, ingredients, recipes` |
| DailySale | `menuId, soldOn, quantity` |
| SalesSnapshot | `rows:List<DailySale>, coveredRanges:List<{from,to}>` — coveredRanges는 COMPLETED import들의 기간(D08) |
| SupplierSnapshot | `id, storeId, name, phone:string?, phoneConfirmed, phoneReadOn:LocalDate?, orderDays:List<DayOfWeek>, orderDaysStatus` |
| ReceiptLot | `receiptLineId, ingredientId, supplierId, purchasedOn, purchaseQuantity:PositiveQty, unitPrice:Won?, productName` |
| LatestPurchaseFact | `ingredientId, supplier:SupplierSnapshot, productName, unitPrice:Won?, purchasedOn, receiptLineId` |
| InventorySnapshot | `ingredientId, asOf:LocalDate, stock:UsageQuantity?, remainingLots:List<{lot:ReceiptLot, remaining:UsageQuantity, expiresOn:LocalDate?}>` |
| WeatherSnapshot | `storeId, targetDate, temperatureC, condition, summaryText, changeText:string?` |
| NearbyEvent | `id, name, place:string?, distanceMeters:int, startAt, endAt, crowdLevel?, expectedVisitors:int?, peakFrom:LocalTime?, peakTo:LocalTime?, detailUrl:string?` |
| ForecastSnapshot | `menu:MenuSnapshot, targetDate, baseline, recommended, adjusted:NonNegativeQty?, produced:NonNegativeQty?, eventDiff:SignedQty, previousRecommended:NonNegativeQty?, summaryText:string?` |
| OrderPlanningLine | `ingredient, supplier, quantity:PurchaseQuantity, stock:PurchaseQuantity?, price:Won?, eventDiff:SignedQty, productName` |
| OrderPlanSnapshot | `targetDate, lines:List<OrderPlanningLine>, events:List<NearbyEvent>, usualTotal:Won?, total:Won?, reducedIngredientIds:List<IngredientId>` |
| DiscountEvaluation | `menu, date, discount:{type,value}, startTime, quantity:PositiveQty, originalPrice:Won, discountedPrice:Won, wasteCostAvoided:Won?, revenueReduction:Won, promoText, channels` |
| DiscountRecommendationsView | `date, baseTime, recommendations:List<{id, evaluation:DiscountEvaluation, expectedSavings:int, reasonText}>, surplusStock:List<{ingredient, quantity:UsageQuantity, wasteCost:Won?, mitigation}>` |
| AlertSnapshot | `id, type:EXPIRY_IMMINENT\|PRICE_CHANGED, title, message:string?, actions:List<OPEN_DISCOUNTS>` |
| Policy | [정책 파일](../../app/policies/defaults.toml)을 읽은 불변 값. 계산 함수에 인자로 넘긴다. |

`GeoPoint={latitude:float[-90,90], longitude:float[-180,180]}`.

## 포트

모든 포트는 storeId를 받는다. event 자료만 가게와 무관한 공통 자료다. DB 세션은 adapter 안에서만 다루고 application에는 트랜잭션 핸들(UoW)로 준다.

| 모듈 / 포트 | 시그니처 | 보장 |
|---|---|---|
| stores / StoreReader | `require(StoreId) → StoreSnapshot` | 없으면 NotFound |
| stores / StoreWriter | `markFirstForecast(UoW, StoreId, Instant) → void` | 최초 예측 행이 생긴 트랜잭션에서 first_forecast_at이 null일 때만 기록 |
| catalog / CatalogReader | `snapshot(StoreId) → CatalogSnapshot` | 모든 참조가 같은 가게 |
| catalog / CatalogWriter | `upsertImportedMenus(UoW, StoreId, List<MenuImportDraft>) → List<MenuSnapshot>` | 이름으로 upsert, 기존 단위·레시피 보존 |
| catalog / CatalogWriter | `resolveReceiptIngredients(UoW, StoreId, List<IngredientResolution>) → List<IngredientSnapshot>` | 영수증 확정에서만 호출 |
| sales / SalesReader | `read(StoreId, from, through) → SalesSnapshot` | 기간 안의 0과 기간 밖을 구분 |
| receipts / ReceiptReader | `lots(StoreId, through) → List<ReceiptLot>` | 확정된 줄만, 날짜·id 순 |
| receipts / PriceReader | `latest(StoreId, List<IngredientId>) → Map<IngredientId, LatestPurchaseFact?>` | 가장 최근 줄 하나에서 단가·거래처·품명을 함께 |
| receipts / PriceReader | `previousPrice(StoreId, IngredientId) → Won?` | 단가 변동 알림용, 최근 두 번째 유효 단가 |
| suppliers / SupplierReader | `list(StoreId) → List<SupplierSnapshot>` | id 순 |
| suppliers / SupplierWriter | `resolve(UoW, StoreId, List<SupplierDraft>) → List<SupplierSnapshot>` | 이름으로 upsert, 확인된 전화·요일 보호 |
| suppliers / SupplierWriter | `confirmPhone(UoW, SupplierId, phone) → void` | recordSent가 호출 |
| forecasting / ForecastWriter | `setAdjusted(UoW, StoreId, LocalDate, MenuId, NonNegativeQty) → ForecastSnapshot` | 기존 행의 adjusted만 교체 |
| forecasting / ForecastReader | `read(StoreId, LocalDate) → List<ForecastSnapshot>` | 저장된 값만 반환 |
| forecasting / ForecastReader | `usage(StoreId, from, through) → List<{menuId, date, produced?, planned}>` | 재고 계산의 '판매 기록 없는 날' 입력 |
| forecasting / ContextReader | `read(StoreId, targetDate) → {weather:WeatherSnapshot?, events:List<NearbyEvent>}` | 반경·기간 조건 적용 완료 |
| forecasting / ForecastCalculator | `build(store, targetDate, menus, sales, weather?, events, policy) → List<ForecastSnapshot>` | IO 없음 |
| inventory / InventoryCalculator | `evaluate(catalog, sales, usage, lots, businessHours, asOf, policy) → List<InventorySnapshot>` | IO 없음 |
| inventory / InventoryReader | `endOf(StoreId, asOf:LocalDate) → List<InventorySnapshot>` | 위 입력을 모아 asOf 영업 종료 시점 재고를 계산. 재고를 읽는 모든 곳이 이것만 쓴다 |
| ordering / PlanOrder | `execute(UoW, StoreContext, targetDate) → OrderPlanSnapshot` | 발주 계획의 유일한 진입점 |
| ordering / OrderPlanner | `plan(catalog, forecasts, inventory, latestPurchase, suppliers, events, weekday, policy) → OrderPlanSnapshot` | IO 없음. PlanOrder 안에서만 호출 |
| discounts / DiscountCalculator | `evaluate(DiscountInput, menu, catalog, inventory, latestPurchase, policy) → DiscountEvaluation` | IO 없음. dryRun과 실제 생성이 같이 씀 |
| discounts / RecommendDiscounts | `execute(StoreContext) → DiscountRecommendationsView` | 조회할 때 계산(D25). 홈·재료 탭·할인 탭 모두 D28 적용 |
| views / BuildAlerts | `execute(StoreContext) → List<AlertSnapshot>` | 알림의 유일한 진입점 |

읽기 포트와 조합 유스케이스는 호출자가 연 UoW의 세션을 함께 쓴다. 쓰기 포트는 flush 후 같은 UoW에서 결과가 보이게 하고 최종 commit은 최상위 유스케이스에서 한다. `reducedIngredientIds`는 C05의 내부 계산 결과이며 공개 OrderPlan 필드에서 제외한다.

`MenuImportDraft={name, unit, price:Won?}`. `IngredientResolution={sourceKey, existingIngredientId 또는 newName, usageUnit?, conversion?, shelfLifeDays?}`. `SupplierDraft={sourceKey, name, phone?, purchasedOnDates}`. `DiscountInput`은 `DiscountCreate`를 검증한 내부 타입이다.

## 작업 payload

작업을 등록할 때 `background_job.payload`에는 아래 입력 필드를 담는다. 파일 내용이나 OCR 결과를 넣지 않고 DB 행을 가리킨다.

| kind | payload | dedupe_key |
|---|---|---|
| GEOCODE_STORE | `storeId` | `geocode:<storeId>` |
| PARSE_SALES | `storeId, importId` | `parse:<importId>` |
| APPLY_SALES | `storeId, importId` | `apply:<importId>` |
| GENERATE_RECIPES | `storeId, importId` | `recipe:<importId>` |
| SCAN_RECEIPTS | `storeId, scanId` | `ocr:<scanId>` |
| REFRESH_CONTEXT | `storeId, targetDate, slot` | `context:<storeId>:<date>:<slot>` |
| GENERATE_FORECAST | `storeId, targetDate, slot`, DATA_CHANGED는 `sourceJobId` 포함 | `forecast:<storeId>:<date>:<slot>` (DATA_CHANGED는 끝에 `:<sourceJobId>`를 붙임) |
| SEND_PUSH | `storeId, targetDate, slot:NIGHT\|MORNING` | `push:<storeId>:<date>:<slot>` |

NIGHT 예측 성공 시 같은 작업의 payload에 내부 결과 `nightReference={recommendedByMenuId, weatherText}`를 남긴다. NIGHT·MORNING 예측은 푸시에 쓸 C07 문구도 같은 작업에 저장한다(예측 항목이 없으면 null). 예측 반영·결과 기록·SUCCEEDED는 같은 commit이다. 같은 대상일 비교와 푸시는 이 결과를 사용한다.

`slot`은 `NIGHT | MORNING | DATA_CHANGED`다. 같은 dedupe_key가 이미 있으면 새로 등록하지 않는다. DATA_CHANGED의 sourceJobId는 후속 예측을 등록한 APPLY_SALES 또는 REFRESH_CONTEXT 작업 ID다. 고정된 시연 시각에서도 서로 다른 변경은 각각 반영되고 같은 작업의 재시도만 합쳐진다.
