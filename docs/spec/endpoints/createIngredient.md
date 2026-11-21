# createIngredient

`POST /stores/{storeId}/ingredients`

재료 직접 등록

## 공개 계약

- operationId: `createIngredient`; 실제 경로에는 `/v1` prefix를 붙인다. 로그인 쿠키의 가게만 접근한다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

요청 본문: `IngredientCreate` `{name, unit}`. 성공 201 `IngredientSummary`.

## 처리 순서

1. name을 NFC 정규화·trim 후 1~40자, unit은 `개` 또는 `g`(D47)인지 검증한다. 아니면 422.
2. 같은 가게에 같은 이름의 재료가 있으면 409.
3. 재료를 만들고 201. 입고가 없으므로 `price=null`, `supplier=null`이다.

## 완료 조건

이름 길이·단위 오류 422, 이름 중복 409, 다른 가게 접근 404를 검증한다.
