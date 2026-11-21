# confirmReceiptScan

`POST /stores/{storeId}/receipt-scans/{scanId}/confirm`

3.6 '저장'

## 공개 계약

- operationId: `confirmReceiptScan`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `scanId` (path): {type: string} — 필수

요청 본문: `application/json`, 생략 가능, `ReceiptScanConfirm`.

| 성공 상태 | 응답 |
|---|---|
| 204 | 본문 없음 |

## 책임과 경계

receipts / `ConfirmReceiptScan.execute(StoreContext, ScanId, answers:List<{questionId,answer:SAME|DIFFERENT}>) → void`. suppliers·catalog의 쓰기 포트를 같은 트랜잭션에 묶는다. 연결·생성 규칙은 [영수증 읽기](../infrastructure/integrations.md)에 있다.

## 처리 순서

1. body 생략이나 `{}`는 `answers=[]`로 본다. questionId 중복은 422. store를 잠그고 scan 소속을 확인한다(아니면 404).
2. PROCESSING은 422, SCAN_RECEIPTS가 DEAD면 500. 이미 CONFIRMED면: body가 없거나 저장된 답과 같으면 204, 다른 답이면 422.
3. NEEDS_REVIEW의 모든 질문에 하나씩 답이 있어야 한다. 모르는 questionId나 빠진 답은 422. 질문이 없으면 body 없이 진행한다.
4. 가게 잠금 안에서 사진별 중복을 다시 판정한다(D22). 중복 사진은 안내만 남기고, 남은 사진에 대해서만 거래처를 이름으로 찾거나 만든다. 확인된 전화번호가 없는 거래처에만 영수증 번호와 날짜를 넣는다.
5. 재료를 연결하거나 만든다(SAME → 후보 재료, DIFFERENT·신규 → 새 LATER 재료). 새 재료의 단위는 그 줄의 단위(개 또는 g)다. 줄의 단위가 연결된 재료의 단위와 다르면 그 줄은 빼고 '못 읽은 줄' 문구로 안내에 더한다(D31).
6. 줄마다 receipt_line(supplier_id, purchased_on, ingredient_id, product_name, quantity, unit_price)을 만든다. quantity는 재료 단위 입고량, unit_price는 `줄 금액 ÷ 입고량`이다(D49). 입고는 여기서만 생긴다.
7. 확인된 요일이 없는 거래처의 주문 요일을 추정한다(D15).
8. 사진별 비교값·반영 여부, 최종 그룹·안내, 답과 새로 만든 id를 `receipt_scan.result`에 저장하고 `status=CONFIRMED`로 바꾼다.
9. 한 번에 commit하고 204. 모든 영수증이 중복이거나 읽지 못한 것이어서 입고가 0건이어도 CONFIRMED다.

## 원자성·실패

거래처·재료·receipt_line·scan 상태 중 일부만 남지 않는다. 같은 scan을 다시 확정해도 입고는 한 번이다(상태 확인). 이름이 비슷하다는 이유로 재료를 합치지 않는다. 합치는 것은 SAME 답과 같은 품명 이력뿐이다.

## 완료 조건

SAME/DIFFERENT, 생략 가능한 body, 모든 질문에 답해야 하는 것, 다른 scan의 질문, 두 번 confirm해도 입고 1회, 다른 scan으로 같은 영수증을 올린 경우, 같은 거래처·같은 날짜에 품목이 다른 두 영수증이 모두 반영되는 것, 재료와 단위가 다른 줄 제외, 확인된 전화·요일 보존, 확정 뒤 재고·발주 조회에 입고가 반영되는 것을 검증한다.
