# getReceiptScan

`GET /stores/{storeId}/receipt-scans/{scanId}`

3.6 거래처별 자동 분류

## 공개 계약

- operationId: `getReceiptScan`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `scanId` (path): {type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `ReceiptScan` |

## 책임과 경계

receipts / `GetReceiptScan.execute(StoreId, ScanId) → ReceiptScanView`. `receipt_scan.result`에 저장된 내부 결과를 공개 타입으로 바꾼다. 읽기 규칙은 [영수증 읽기](../infrastructure/integrations.md)에 있다.

## 처리 순서

1. store/scan 소속을 확인한다(아니면 404). SCAN_RECEIPTS 작업이 DEAD면 500, 재시도 중이면 PROCESSING.
2. PROCESSING: id/status만 준다. groups/questions/removedDuplicateTexts는 생략한다.
3. NEEDS_REVIEW: `groups`는 필수(읽은 것이 없으면 빈 배열), `questions`와 `removedDuplicateTexts`도 빈 배열까지 명시한다. groups는 거래처 이름 순, receipts는 purchasedOn 순, lines는 읽은 순서다.
4. 기존 거래처·재료는 실제 id와 `isNew=false`, 새것은 `id=null`, `isNew=true`, name. `quantity`는 구매 단위다. 단가·전화번호·품명은 공개 스키마에 없으므로 내보내지 않는다.
5. 질문 id는 result에 저장된 값 그대로다. 폴링할 때마다 바뀌지 않는다. `candidate`는 이 가게의 기존 재료다.
6. `removedDuplicateTexts`에는 중복으로 뺀 영수증과 읽지 못해 뺀 사진·줄의 안내가 들어 있다(D22, D23).
7. CONFIRMED: 확정 때 중복을 다시 확인한 최종 그룹·안내를 반환한다. 반영한 거래처·재료의 id는 실제 id다. `questions=[]`.

## 완료 조건

상태별 조건부 스키마, 신규/기존 id와 isNew, 질문 id 고정, CONFIRMED 뒤에도 조회 가능, 단가·전화 미노출, 중복·읽기 실패 안내 문구, 다른 store 404를 검증한다.
