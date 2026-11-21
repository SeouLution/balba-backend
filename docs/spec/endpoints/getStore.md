# getStore

`GET /stores/{storeId}`

가게 정보 조회

## 공개 계약

- operationId: `getStore`; 실제 경로에는 `/v1` prefix를 붙인다. 로그인 쿠키의 가게만 접근한다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

요청 본문 없음. 성공 200 `Store`.

## 처리 순서

1. 가게를 읽어 createStore 응답과 같은 `Store`(id, name, category, address, businessHours)로 돌려준다. 좌표, push token, 생성 상태는 내보내지 않는다.

## 완료 조건

수정 직후 조회 값이 수정 응답과 같은 것, 다른 가게 404를 검증한다.
