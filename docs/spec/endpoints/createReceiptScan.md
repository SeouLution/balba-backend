# createReceiptScan

`POST /stores/{storeId}/receipt-scans`

1.5 · 3.5 '다 찍었어요' (여러 장 한 번에)

## 공개 계약

- operationId: `createReceiptScan`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문: `multipart/form-data`, 필수, 인라인 타입 (아래).

```yaml
type: object
required:
- images
properties:
  images:
    type: array
    maxItems: 30
    items:
      type: string
      contentMediaType: image/jpeg
```

| 성공 상태 | 응답 |
|---|---|
| 202 | `{ id: string }` |

## 책임과 경계

receipts / `CreateReceiptScan.execute(StoreId, images:List<UploadStream>) → ScanId`. 사진 저장과 작업 접수만 동기로 한다.

## 처리 순서

1. store 실존, images 1~30장, 장당·전체 크기 한도(정책 파일 `[upload]`), JPEG 시그니처를 검사한다. 0장은 422.
2. 올린 순서대로 파일을 저장한다.
3. store를 잠그고 receipt_scan(PROCESSING, `images`=저장 위치 배열)을 만들고 SCAN_RECEIPTS 작업을 같은 트랜잭션에 등록한다.
4. commit 후 202 `{id}`. 읽기, 거래처 생성, 입고는 아직 하지 않는다.

## 실패·재요청

사진 한 장이라도 잘못되면 접수 전체가 422. 저장소/DB 오류는 500. 같은 사진을 다시 올리면 새 scan이 되지만, 이미 확정된 같은 영수증은 읽기 단계에서 중복으로 빠진다(D22).

## 완료 조건

1/30/31장, 빈 목록, JPEG가 아닌 파일, 크기 한도, 202 직후 재시작해도 읽기가 끝나는 것, 접수 시점에 입고 0건인 것을 검증한다.
