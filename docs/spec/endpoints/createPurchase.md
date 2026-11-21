# createPurchase

`POST /stores/{storeId}/purchases`

영수증 없는 재료 수기 입고

## 공개 계약

- operationId: `createPurchase`; 실제 경로에는 `/v1` prefix를 붙인다. 로그인 쿠키의 가게만 접근한다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

요청 본문: `ManualPurchaseCreate` `{ingredientId, purchasedOn?, quantity, isStockReset?}`. 성공 201 `ManualPurchase`.

## 처리 순서

1. purchasedOn이 없으면 오늘(KST)이다. 미래 날짜는 422.
2. quantity는 재료 단위(개 또는 g)다. 0보다 커야 하고, isStockReset=true면 0도 허용한다. 음수는 422.
3. 재료가 이 가게에 없으면 404.
4. `receipt_line`에 `source=MANUAL`, `supplier_id=null`, `product_name=null`, `unit_price=null`로 저장한다(D53). isStockReset=true면 이 줄부터 장부를 다시 시작한다(D54).
5. 201. 수기 입고는 재고(C04)에만 반영되고 최근 거래처·단가(C03)와 거래처 주문 요일 추정에는 쓰지 않는다.

## 완료 조건

기본 입고일, 미래 날짜·음수·0(재고 설정 아님) 422, 없는 재료 404, 재고 설정 후 재고 조회 값을 검증한다.
