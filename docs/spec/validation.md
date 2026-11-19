# 검증 기준과 인수 시나리오

아래는 구현 작업의 완료 조건이다. 각 엔드포인트 문서의 완료 조건을 함께 실행한다.

## 검증 계층

| 계층 | 대상 | 방법 |
|---|---|---|
| 계약 | 25개 method/path/operationId, 요청·응답 모양 | `openapi.yaml`을 직접 읽어 라우트와 대조하고, 실제 HTTP 응답을 그 스키마(JSON Schema 2020-12)로 검증 |
| 단위 | 예측·재고·발주·할인 계산, 문장 템플릿 | 고정 Clock, Decimal, 정책 값 fixture. 외부 IO 없음 |
| 통합 | PostgreSQL 제약/잠금/트랜잭션/작업 | 실제 PostgreSQL + 임시 파일 저장소 + fake 외부 포트 |
| 인수 | 온보딩 → 홈 → 발주 → 영수증 → 할인 | FastAPI client와 실제 작업 handler, 고정 Clock |
| 복구 | 재시작/재시도/DEAD | commit 전후 중단, 같은 작업 두 번 실행 |
| 공급자 매핑 | 외부 응답 → 내부 타입 | `tests/fixtures/providers/`에 저장한 실제 응답으로 검증. 기본 테스트는 실제 API를 부르지 않음 |
| 운영 회귀 | 기존 배포 계약 | `/health` GIT_SHA, `/openapi.yaml` 원본 byte 일치, `/scalar`, ARM64 이미지 |

if/then 조건, Decimal의 JSON number, nullable과 필드 생략, 204 빈 body는 실제 응답으로 검증한다.

## 공통 계약 매트릭스

25개 operation 각각에 대해 정상 상태 코드와 body, 없는 store, 다른 store의 하위 ID, 구조 오류, 예상하지 못한 adapter 실패를 확인한다.

- 모든 오류 body가 `{code}` 한 키뿐이다. FastAPI detail, stack, 외부 서비스 문구가 없다.
- 수량에 bool/NaN/Infinity/음수는 422. 소수 6자리 초과는 6자리로 반올림돼 저장된다(D26).
- 모르는 값: 선택 non-nullable 필드는 생략, 필수 nullable 필드는 null로 존재.
- body 없는 confirmSalesImport 202와 모든 204는 bytes 길이 0.
- 다른 store의 menu/supplier/ingredient/import/scan/order ID는 조회와 batch 양쪽에서 404.
- 빈 목록, 0, null을 서로 바꾸지 않는다.
- batch의 한 항목이 오류면 DB 전체가 그대로다.

## 계산 기준 fixture

### F01 예측

대상일 2026-11-11(수), 같은 요일 네 판매량 40/60/50/50 → baseline=50. 날씨·행사 계수 1.0이면 recommended=50, eventDiff=0, direction=SIMILAR. 행사 계수 1.2면 recommended=60, eventDiff=10, diff=10, MORE. 날씨 계수 0.9까지 적용하면 recommended=54, eventDiff=9.

baseline 58.25, 행사 1.2 → recommended=70, diff=roundHalfUp(11.75)=12, summaryText `지난 4주 토요일 평균 58줄 · 행사 +20%`.

판매 기록이 없는 주 1개는 표본에서 빠져 세 표본 평균이 된다. 파일 기간 안에서 행이 없는 날은 0으로 표본에 들어간다. 네 주 모두 기록이 없으면 예측을 만들지 않는다. `adjusted=0`이면 planned=0이고 예측을 다시 만들어도 0이다.

밤사이 변경: 밤 추천 50 → 아침 다시 만들어 48이면 previousRecommended=50. 아침에 한 번 더 만들어 48이면 그대로 50. 다시 50이 되면 previousRecommended=null.

### F02 재고·발주

김밥 1줄당 계란 1개, 1판=30개.

- 11/8 계란 2판 입고(60개). 11/8~11/9는 판매 기록이 있고 김밥이 20줄, 25줄 팔림 → 11/9 끝 재고 15개.
- 11/10은 판매 기록이 없고 예측 행이 있다. produced=null, planned=10 → 11/10 끝 재고 5개. produced=12를 기록하면 3개.
- 11/10에 예측 행도 없으면 지난 4주 화요일 판매 평균을 쓴다. 그 표본도 없으면 stock=null.
- 사용량이 재고보다 많은 날은 재고 0으로 두고 다음 날로 넘어간다.
- 계란 유통기한 21일이면 11/8 입고분의 추정 기한은 11/28이다.

발주: 쓸 수 있는 재고 15개, 계획 75줄 → 부족 60개 → 2판. 행사 없는 준비량 60줄이면 부족 45개 → 2판, eventDiff=0. stock=null이면 75개 전체 → 3판. 1판 8,900원 → 2판 17,800원.

금액: 줄 3개 중 단가를 아는 줄이 2개면 합계는 그 2개의 합이다. 하나도 모르면 null. 줄이 없으면 `suppliers=[]`, `Home.order=null`.

발주서 생성, 전송 기록, 할인 생성만으로는 재고가 바뀌지 않는다. 만든 양 PUT은 판매 기록이 없는 날의 재고를 바꾼다.

### F03 할인

판매가 5,000원, 10%, 5개 → 할인가 4,500원, revenueReduction 2,500원. 소진되는 임박 lot 원가 9,000원 → expectedSavings 6,500원.

임박 재료의 단가를 모르면 수동 할인은 만들 수 있고 응답에 `wasteCostAvoided` 키가 없다. 추천 후보에서는 빠진다.

떡볶이와 어묵탕이 같은 임박 어묵을 쓰면, 먼저 고른 메뉴가 쓴 만큼을 빼고 둘째 메뉴를 계산한다. 두 추천의 wasteCostAvoided 합이 어묵 임박 재고의 원가를 넘지 않는다.

16:59 요청은 빈 목록, 17:00 요청은 계산 결과다. 추천 시작 시각은 마감 21:00이면 19:00이다.

## 인수 흐름

### A01 온보딩

1. 가게 생성 201 → 푸시 구독 등록 204.
2. 판매 업로드 202 → PARSE_SALES(열 판별 fake) → NEEDS_CONFIRMATION의 columnMapping/preview 확인.
3. confirm 202 → APPLY_SALES → COMPLETED. confirm을 반복해도 판매합이 같다.
4. GENERATE_RECIPES 완료 → candidates READY(배지 포함) → CORE 확정 204.
5. 영수증을 건너뛰고 recipes GET(핵심 재료 항목만) / PUT.
6. 홈 200: 예측 있음, FIRST_FORECAST 배너, `order=null`(거래처 없음), cautions=[].

### A02 영수증과 발주

1. JPEG 여러 장 접수 202, PROCESSING → NEEDS_REVIEW. receipt_line은 아직 0건.
2. 같은 영수증을 두 번 넣은 경우 한 장만 남고 안내가 온다. 읽지 못한 사진은 빠지고 안내가 온다.
3. 질문에 SAME/DIFFERENT로 답하고 confirm 204 → 거래처·재료·입고가 한 번에 생긴다.
4. 같은 scan 재확정 204, 같은 영수증을 다른 scan으로 다시 올려도 입고가 늘지 않는다.
5. 같은 품명이 다음 scan에 다시 나오면 질문 없이 같은 재료에 연결된다.
6. 주문 요일 확정 204 → getOrderPlan에 오늘 주문 요일인 거래처만 나온다. 줄마다 stock이 숫자로 나온다.
7. 준비량 변경 200 → 응답의 item과 order가 같은 값을 반영한다.
8. 발주서 생성 201 → send-records 204. 재고는 그대로이고 생성 당시 금액이 고정된다.

### A03 당일 아침과 할인

1. 자정이 지나면 홈이 TODAY, targetDate 오늘. 어젯밤 보낸 발주서가 있으면 DELIVERY_EXPECTED에 품목이 나열된다.
2. produced PUT 204 → 홈에 반영되고 daily_sales는 그대로다.
3. 추정 기한이 오늘인 재고가 남으면 cautions에 EXPIRY_IMMINENT가 나온다.
4. 17:00 이후 추천 조회 200: recommendations와 surplusStock이 나온다. 조회 전후로 DB 행 수가 같다.
5. dryRun 200 전후로 discount 행 수가 같다. 실제 생성 201 뒤 이력에 정확히 1건이 늘어난다.
6. 다음 날 menu.price가 바뀌어도 과거 Discount의 originalPrice/discountedPrice/promoText는 그대로다.

### A04 실패와 복구

202 직후 서버 재시작, 외부 호출 timeout, 파일 없음, AI의 스키마 위반, 열을 찾지 못한 엑셀, 전부 읽지 못한 영수증, 같은 가게 동시 PUT을 각각 재현한다. 상태가 끝없이 진행 중으로 남지 않고 성공 또는 DEAD(조회 500)로 끝나며, 업무 데이터가 두 번 반영되거나 일부만 남지 않는다.

## 구현 완료 체크

- 25개 엔드포인트 문서의 완료 조건을 operationId별 테스트에 연결했다.
- Alembic migration이 빈 DB에 적용된다. 테이블·컬럼이 `erd.dbml`과 일치한다.
- 외부 포트마다 fake와 실제 adapter가 같은 계약을 만족한다. 실제 adapter는 저장한 실제 응답 fixture로 검증했다.
- 모듈 경계를 ORM 객체나 임의 dict로 넘기지 않는다.
- `openapi-spec-validator openapi.yaml`, `ruff check .`, `pytest -q`를 통과한다.
- OpenAPI/ERD와 다르게 구현한 부분이 없다. 생겼다면 테스트로 가리지 않고 결정 원장에 적는다.
