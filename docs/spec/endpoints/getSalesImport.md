# getSalesImport

`GET /stores/{storeId}/sales-imports/{importId}`

1.3 열 확인 (진행 상태 조회)

## 공개 계약

- operationId: `getSalesImport`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `importId` (path): {type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 200 | `SalesImport` |

## 책임과 경계

sales / `GetSalesImport.execute(StoreId, ImportId) → SalesImportView`. DB 상태를 public enum으로 투영한다.

## 처리 순서

1. store/import 소속을 한정해 읽는다. 다른 store의 ID도 404다.
2. 현재 parse/apply job의 terminal DEAD가 있으면 500. 재시도 중이면 현재 public 상태를 반환한다.
3. RECEIVED/PARSING에서는 id/status를 필수로 보내고 posFormat/detection/period/recordCount는 저장된 사실이 없으면 null.
4. NEEDS_CONFIRMATION에서는 detection이 non-null이며 columnMapping 필수, preview≤5. columnMapping에서 찾지 못한 열은 null이고, `columns`에 첫 줄 열 이름 목록을 준다. 원본 경로·parser 오류를 제거한다. period와 recordCount는 parse 결과를 전달한다.
5. APPLYING/COMPLETED에서도 같은 detection/period/count를 유지한다. 판매 적용 완료와 레시피 READY는 별개이며 이 endpoint가 생성 상태를 추가하지 않는다.

## 완료 조건

모든 상태의 스키마 검증, 조건부 required 검증, apply 재시도 중 동일 detection, 영원히 PARSING으로 보이지 않는 DEAD 처리, 소속 검증을 수행한다. GET가 job을 새로 만들거나 상태를 전이하지 않는다.
