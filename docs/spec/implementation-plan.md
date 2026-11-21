# 구현 순서와 재사용 프롬프트

## 의존 순서

| 단계 | 입력 문서 | 산출물 | 다음 단계로 가는 조건 |
|---|---|---|---|
| P0 계약 기반 | 범위, HTTP 규칙, `openapi.yaml` | DTO, 공통 오류, 25개 라우트(미구현은 500), 계약 테스트 틀 | 25 operation이 `openapi.yaml`과 1:1 대응 |
| P1 인프라 | 아키텍처, 경계 타입, persistence, runtime | 설정, UoW, ORM/Alembic(ERD 16개 테이블), Clock(`DEMO_NOW`), FileStore, 정책 파일 로더 | 실제 PostgreSQL에서 migration·rollback 통과, 기존 health/spec/scalar 회귀 통과. `docs/spec/policy-defaults.toml`을 `app/policies/defaults.toml`로 옮김 |
| P2 작업 루프 | jobs | `background_job` 큐, 작업 루프, 스케줄 등록, 외부 포트의 fake | 접수 후 재시작/재시도/DEAD/dedupe 및 시연 slot 1회 등록 검증 |
| P3 가게·판매 | createStore, putPushToken, sales 3개, integrations(엑셀 읽기, 주소→좌표) | store, 엑셀 읽기, 판매 반영 | 같은 칸 다시 올리면 교체, COMPLETED와 후속 작업이 한 트랜잭션 |
| P4 catalog·레시피 | candidates, core, recipes 2개, integrations(레시피 초안) | 재료·레시피 초안, 배지, 핵심 재료 선택, 레시피 저장 | LATER 레시피 보존, GENERATING 중 쓰기 422 |
| P5 영수증·거래처 | receipts 3개, suppliers 2개, integrations(영수증 읽기) | 영수증 읽기·확정, 입고·단가, 주문 요일 | 중복 입고 없음, 묶음 rollback, 확인된 전화·요일 보존 |
| P6 예측·재고·발주 | calculations C01~C05, setProduction, getOrderPlan, integrations(날씨, 행사) | 예측·재고·발주 계산, `PlanOrder`, 날씨·행사 갱신 | F01·F02 fixture 통과 |
| P7 할인 | C06·C07 할인 문구, discounts 3개, P6 PlanOrder | 추천 계산, preview/생성/이력 | F03 통과, GET 추천을 그대로 preview 가능 |
| P8 화면 조합·발주서 | getHome, adjustForecast, listIngredients, purchaseOrders 2개, recordSent, C07, P7 추천 | `views` 유스케이스, 발주서 문구, 전송 기록 | 홈·준비량 조정·발주가 P6 계산을 공유 |
| P9 실연동·운영 | integrations, runtime, validation | 실제 adapter 6종과 응답 fixture, 푸시, Dockerfile·배포 변경 목록 | 전체 인수 조건 통과, 실제 키로 각 공급자 1회 이상 호출 확인 |

P3~P8은 외부 포트를 fake로 두고 진행할 수 있다. 실제 adapter는 P9에서 붙이되, 키가 없어 호출해 보지 못한 공급자를 완료로 보고하지 않는다.

## 전체 구현 프롬프트

```text
이 저장소의 SeouLution 백엔드를 docs/README.md와 docs/spec/ 이하 명세대로 구현해.

먼저 현재 git 변경사항, openapi.yaml, docs/erd.dbml을 확인해.
docs/spec/00-scope-and-decisions.md의 결정 원장과 원본 해시를 확인하고,
원본이 달라졌다면 영향받는 문서를 먼저 고쳐. 사용자 변경을 덮지 마.

공개 API의 기준은 openapi.yaml, 저장 구조의 기준은 docs/erd.dbml 하나야.
둘에 없는 필드·테이블·엔드포인트를 만들지 마. 필요해 보이면 구현을 멈추고 나에게 물어봐.
숫자 기본값은 docs/spec/policy-defaults.toml에서만 읽어(P1에서 app/policies/defaults.toml로 옮겨).

docs/spec/01-architecture.md, 02-boundary-contracts.md, 03-http-contract.md,
domain/calculations.md, infrastructure/ 문서를 선행 기준으로 써.
implementation-plan.md의 P0~P9 순서로 구현하고 endpoints/의 25개 operation을 모두 완성해.
애플리케이션 코드, Alembic migration, 테스트, 실행 설정까지 구현해.

프로세스는 uvicorn 1개야. 비동기 작업은 background_job 테이블과 API 프로세스 안의 작업 루프로 처리해.
발주 계획은 ordering.PlanOrder, 재고는 inventory.InventoryReader 한 곳에서만 계산해.
서버가 만드는 문장은 calculations.md C07의 템플릿만 써.

외부 연동은 integrations.md의 공급자를 실제로 붙여. 요청·응답 필드는 공식 문서를 확인하고,
실제 응답을 tests/fixtures/providers/에 저장해 매핑 테스트를 써. 기억으로 필드 이름을 쓰지 마.
키가 없어 호출하지 못한 공급자는 fake로 테스트만 통과시키고, 완료가 아니라고 보고해.

결정 원장의 미정 정책은 구현 기본값으로 정하지 말고 남은 결정으로 보고해. 각 단계의 확정된 완료 조건과 validation.md의 테스트를 통과시켜.
기존 /health(GIT_SHA), /openapi.yaml, /scalar와 포트 8000/ARM64 배포 계약을 유지해.
완료 보고에는 구현한 operation 목록, 실제로 실행한 검증, 호출해 보지 못한 공급자, 배포 서버에서 바꿔야 할 것을 적어.
별도 요청 없이 commit/push/배포하지 마.
```

## 단계별 구현 프롬프트

`<단계/operationId>`만 바꿔 쓴다.

```text
docs/spec/implementation-plan.md의 <단계/operationId>를 구현해.
docs/README.md의 읽는 순서대로 공통 문서와 해당 엔드포인트 문서를 먼저 읽어.
선행 단계가 실제로 구현돼 있는지 확인하고, 없는 선행 타입/포트/테이블은 함께 구현해.
결정 원장의 미정 사례는 별도로 보고하고 나머지 완료 조건을 진행해.
다른 모듈이 소유한 테이블에 직접 쓰지 말고 정의된 포트를 써.
openapi.yaml과 erd.dbml을 바꾸지 말고, 입력/출력/오류/원자성/재요청/완료 조건을 모두 구현해.
엔드포인트 문서의 완료 조건과 관련 validation.md fixture를 실행해.
검증 결과를 보고해. commit/push는 하지 마.
```

## 계약을 바꿔야 할 때

1. 바꿀 곳을 `operationId + JSON pointer` 또는 `테이블.컬럼`으로 적는다.
2. `openapi.yaml` 또는 `erd.dbml`을 먼저 고친다.
3. [결정 원장](00-scope-and-decisions.md)에 항목을 추가하고 원본 해시를 갱신한다.
4. 영향받는 엔드포인트·계산·검증 문서를 함께 고친다.
