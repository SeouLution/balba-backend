# confirmSalesImport

`POST /stores/{storeId}/sales-imports/{importId}/confirm`

1.3 열 확인

## 공개 계약

- operationId: `confirmSalesImport`; 실제 경로에는 `/v1` prefix를 붙인다.
- 모든 오류는 [HTTP 공통 계약](../03-http-contract.md)의 `{code}`만 반환한다.
- 공통 타입은 [경계 계약](../02-boundary-contracts.md), 공개 필드는 [openapi.yaml](../../../openapi.yaml)을 따른다.

입력 파라미터:

- `storeId` (path): {examples: [st_8f2k1], type: string} — 필수
- `importId` (path): {type: string} — 필수

요청 본문 없음.

| 성공 상태 | 응답 |
|---|---|
| 202 | 본문 없음 |

## 책임과 경계

sales / `ConfirmSalesImport.execute(StoreId, ImportId) → Accepted`. 요청 본문이 없다. 1.3에서 사장님이 확인한 열 매핑(저장된 detection)을 승인한다.

## 처리 순서

1. store를 잠그고 import가 이 가게 것인지 확인한다(아니면 404).
2. RECEIVED/PARSING이면 422. PARSE/APPLY 작업이 DEAD면 500. APPLYING/COMPLETED면 아무것도 하지 않고 202 빈 body.
3. NEEDS_CONFIRMATION이면 상태를 APPLYING으로 바꾸고 `apply:<importId>` 작업을 같은 트랜잭션에 등록한다.
4. 202 빈 body.

## 반영(APPLY_SALES)의 의미

[엑셀 읽기](../infrastructure/integrations.md)의 반영 규칙을 따른다. 한 트랜잭션에서 메뉴 upsert → daily_sales 칸 교체 → COMPLETED → 후속 작업 등록(레시피 없는 메뉴가 있으면 GENERATE_RECIPES, 예측 모델을 다시 계산하는 FIT_FORECAST_MODEL)을 한다. 오늘·내일 예측은 FIT_FORECAST_MODEL이 끝날 때 등록된다([작업 명세](../infrastructure/jobs.md)).

- 같은 메뉴·날짜는 더하지 않고 새 파일의 합계로 교체한다.
- 첫 파일 기간 안에서 행이 없는 메뉴·날짜는 0개 판매로 읽힌다(D08). 겹치는 새 파일에서 누락된 기존 판매의 처리는 미정(U02)이다.
- 이미 레시피가 있는 메뉴는 다시 만들지 않는다.

## 완료 조건

동시 confirm 2건에 작업 1개, 반영 중 중단 시 rollback 후 재실행, COMPLETED 재요청 무변경, 첫 파일 기간 안의 빈 칸을 0으로 읽는 것, 후속 작업이 같은 트랜잭션에 등록되는 것을 검증한다.
