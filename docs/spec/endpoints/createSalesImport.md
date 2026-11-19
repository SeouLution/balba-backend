# createSalesImport

`POST /stores/{storeId}/sales-imports`

1.2 이 기기에서 엑셀 올리기

## 공개 계약

- operationId: `createSalesImport`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수

요청 본문: `multipart/form-data`, 필수, [OpenAPI](../../../openapi.yaml)의 `createSalesImport → requestBody → multipart/form-data`.

| 성공 상태 | 응답 |
|---|---|
| 202 | `{ id: string }` |

## 책임과 경계

sales / `CreateSalesImport.execute(StoreId, UploadStream) → ImportId`. 파일 저장과 DB 접수까지만 동기 실행한다. 열 판별(AI)과 읽기 규칙은 [엑셀 읽기](../infrastructure/integrations.md)에 있다.

## 처리 순서

1. store를 확인하고 multipart의 file 1개, 확장자/시그니처/크기를 검증한다. 파일 내용의 열 판별은 worker 책임이다.
2. FileStore에 스트리밍하여 sha256/byteSize/key를 얻는다. 클라이언트 파일명을 key로 사용하지 않는다.
3. store 잠금 아래 sales_import(RECEIVED, file_path)을 만들고 PARSE_SALES job을 같은 UoW에 넣는다.
4. commit 후 202 `{"id":"<importId>"}`. 원본 bytes나 판매 기간을 이 응답에 추가하지 않는다.

## 실패·재요청

업로드 검증 실패 422, store 없음 404, 저장소/DB 장애 500. 파일 저장 뒤 DB가 실패해 남은 파일은 참조가 없으므로 운영자가 지워도 된다. 같은 파일을 다시 올려도 새 import ID이지만 이후 판매 반영은 셀 교체여서 중복 가산되지 않는다. 파싱 실패는 접수된 리소스의 조회 500으로 노출된다.

## 완료 조건

xlsx/xls/csv 시그니처, 0바이트·한도·다른 확장자, 202 직후 프로세스 종료, file persist/DB commit 경계를 검증한다. 접수 단계에서 외부 AI가 응답을 지연시키지 않는다.
