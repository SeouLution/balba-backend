# createStore

`POST /stores`

1.1 가게 정보 저장

## 공개 계약

- operationId: `createStore`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

요청 본문: `application/json`, 필수, `StoreCreate`.

| 성공 상태 | 응답 |
|---|---|
| 201 | `Store` |

## 책임과 경계

stores / `CreateStore.execute(storeId, StoreCreateInput) → StoreSnapshot`. storeId는 쿠키 `access_token`으로 찾은 계정의 `app_user.store_id`다(가입 때 발급). StoreCreateInput은 name/category/roadAddress/7개 DayHours의 검증된 타입이다. 생성 응답은 Store이며 push token, generationStatus, 좌표는 노출하지 않는다.

## 처리 순서

1. name trim 후 1~40자, 주소 trim 후 nonempty, category enum을 검증한다. DayHours는 MON~SUN 각각 한 개, 영업일 open/close 필수다. 휴무일 시간 값은 저장 시 제거한다.
2. 계정의 store_id로 store 행을 만들고 name, category, road_address, business_hours를 MON→SUN 순으로 저장한다. 좌표/push_token/first_forecast_at은 null, generation_status=GENERATING이다.
3. 동일 UoW에 GEOCODE_STORE job을 넣는다. 외부 주소 API를 기다리지 않는다. 메뉴가 없으므로 이 시점에 AI 레시피 job은 만들지 않는다.
4. commit 후 저장한 값에서 Store DTO를 만들어 201. 주소 정규화 실패가 가게 생성을 취소하지 않는다.

## 원자성·실패

store와 job은 함께 저장된다. 계정당 가게는 하나다. 이미 store 행이 있는 계정의 POST는 409다. 쿠키가 없거나 잘못되면 401, DB 장애는 500, 요청 구조/요일 오류는 422. 생성 응답 유실 후 재시도는 409를 받으므로 프론트는 `/users/me`의 storeId로 이어 가면 된다.

## 완료 조건

- 7개 요일 정상/중복/누락, 전부 휴무, 심야 영업을 검증한다.
- 외부 주소 실패에도 201, worker 복구 후 좌표만 반영된다.
- 생성 직후 recipes 조회는 GENERATING+빈 목록이다.
