# updateStore

`PUT /stores/{storeId}`

가게 정보 수정

## 공개 계약

- operationId: `updateStore`; 실제 경로에는 `/v1` prefix를 붙인다. 로그인 쿠키의 가게만 접근한다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

요청 본문: `StoreCreate` `{name, category, address, businessHours}`. 성공 200 `Store`.

## 처리 순서

1. [createStore](createStore.md)와 같은 규칙으로 검증한다(name 1~40자, 주소 nonempty, category enum, 요일별 DayHours 7개).
2. 가게 행을 잠그고 name, category, road_address, business_hours를 바꾼다.
3. 주소가 바뀌면 좌표를 null로 비우고 GEOCODE_STORE 작업을 새로 넣는다. 좌표를 찾으면 오늘·내일 예측을 DATA_CHANGED로 다시 만든다.
4. 주소는 그대로이고 영업시간이 바뀌면 오늘·내일 예측을 DATA_CHANGED로 다시 만든다.
5. 200 `Store`.

## 완료 조건

검증 오류 422, 다른 가게 404, 주소 변경 시 좌표 초기화와 재조회 작업, 영업시간 변경 시 예측 재계산을 검증한다.
