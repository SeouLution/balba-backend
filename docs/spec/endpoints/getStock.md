# getStock

`GET /stores/{storeId}/stock`

재료별 추정 재고

## 공개 계약

- operationId: `getStock`; 실제 경로에는 `/v1` prefix를 붙인다. 로그인 쿠키의 가게만 접근한다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

쿼리: `asOf`(선택, YYYY-MM-DD, 기본 오늘, 미래 422). 성공 200 `StockList`.

## 처리 순서

1. 가게의 모든 재료에 대해 [C04](../domain/calculations.md)로 asOf 영업 종료 시점 재고를 계산한다.
2. 줄마다 `stock`(재료 단위 Quantity, 입고가 없으면 null), `startedOn`(장부 시작일), `warning`(재고가 음수면 `입고 기록이 누락됐을 수 있어요`, 아니면 null)을 준다(D55).
3. 재료는 ingredientId 순이다.

## 완료 조건

입고 없는 재료 null, 음수 재고 경고, 재고 설정 이후만 계산, 미래 asOf 422를 검증한다.
