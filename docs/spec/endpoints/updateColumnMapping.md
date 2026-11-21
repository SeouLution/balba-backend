# updateColumnMapping

`PUT /stores/{storeId}/sales-imports/{importId}/column-mapping`

1.3 열 바꾸기

## 공개 계약

- operationId: `updateColumnMapping`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `importId` (path): {type: string} — 필수

요청 본문: `application/json`, 필수, `ColumnMapping`. 바꿀 열만 보낸다. 보내지 않았거나 null인 열은 저장된 값을 그대로 둔다.

| 성공 상태 | 응답 |
|---|---|
| 200 | `SalesImport` |

## 책임과 경계

sales / `UpdateColumnMapping.execute(StoreId, ImportId, Partial<ColumnMapping>) → SalesImportView`. AI가 열을 찾지 못했거나 잘못 골랐을 때 사장님이 1.3에서 열을 직접 고른다.

## 처리 순서

1. store/import 소속을 확인한다(아니면 404). PARSE/APPLY 작업이 DEAD면 500.
2. NEEDS_CONFIRMATION이 아니면 422.
3. 원본 파일을 다시 읽는다. 보낸 열 이름이 첫 줄에 없으면 422.
4. 저장된 매핑에 보낸 열을 합치고, [엑셀 읽기](../infrastructure/integrations.md)의 4~5번을 다시 실행해 detection·period·recordCount를 교체한다. 세 열이 다 정해지지 않았으면 preview `[]`, period null, recordCount 0이다.
5. store를 잠근 뒤 상태가 여전히 NEEDS_CONFIRMATION일 때만 저장한다. 그사이 confirm됐으면 422.
6. 200과 getSalesImport와 같은 형태의 결과.

파일을 요청 안에서 읽으므로 큰 파일은 응답이 몇 초 걸릴 수 있다. 여러 번 보내도 된다.
