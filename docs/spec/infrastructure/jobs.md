# 비동기 작업, 상태 전이, 스케줄

## 실행 방식

작업은 `background_job` 테이블에 저장하고, API 프로세스가 시작할 때 띄우는 **작업 루프 1개**가 실행한다(D05). 다른 문서에서 "worker"라고 쓴 곳은 이 작업 루프를 뜻한다. 별도 프로세스가 아니다.

- 업무 행과 작업 행을 같은 트랜잭션에 저장한다. 202는 파일 저장과 이 commit이 끝난 뒤에만 돌려준다.
- 루프는 2초마다 `state=READY`이고 `available_at`이 지난 작업을 `created_at` 순으로 하나 꺼내 `RUNNING`으로 바꾸고 handler를 실행한다. 한 번에 하나만 실행한다.
- 업무 결과를 DB에 반영하는 handler는 후속 작업·결과와 `SUCCEEDED`를 같은 트랜잭션에 기록한다. 실패하면 `attempts`를 올리고 30초/120초 뒤로 `available_at`을 미뤄 `READY`로 되돌린다. 3번째 실패에서 `DEAD`. 입력이 잘못돼 다시 해도 같은 결과인 실패는 바로 `DEAD`.
- 프로세스가 시작할 때 `RUNNING`으로 남은 작업을 모두 `READY`로 되돌린다. 서버가 작업 도중 꺼졌다는 뜻이기 때문이다.
- handler 하나의 외부 호출은 전체 90초 안에 끝낸다. 영수증 30장처럼 오래 걸리는 작업은 handler 안에서 장 단위로 나눠 호출한다.
- 같은 작업이 두 번 실행돼도 업무 반영은 한 번이어야 한다. handler는 먼저 업무 상태를 확인하고, 이미 반영된 상태면 아무것도 하지 않고 성공으로 끝낸다.

프로세스가 1개이므로 작업 선점 잠금과 heartbeat는 없다. 서버를 2대 이상으로 늘리면 그때 `FOR UPDATE SKIP LOCKED`와 만료 시각을 추가한다.

## 판매 import 상태

| 현재 | 트리거 | 다음 | 같은 commit에서 하는 일 |
|---|---|---|---|
| 없음 | createSalesImport | RECEIVED | 파일 위치 저장 + PARSE_SALES 작업 |
| RECEIVED | PARSE_SALES 시작 | PARSING | 상태 전환 |
| PARSING | 열 판별 성공 | NEEDS_CONFIRMATION | detection, pos_system, period, record_count |
| NEEDS_CONFIRMATION | confirmSalesImport | APPLYING | APPLY_SALES 작업 |
| APPLYING | APPLY_SALES 성공 | COMPLETED | menu, daily_sales, 판매가 + GENERATE_RECIPES(새 메뉴가 있을 때) + 오늘·내일 GENERATE_FORECAST |

APPLY_SALES는 저장된 detection의 열 매핑으로 파일을 다시 읽어 반영한다. 파싱 결과를 따로 저장하지 않는다. commit 전에 서버가 꺼지면 업무 변경은 없고 재시작 뒤 다시 실행된다. confirm 요청이 RECEIVED/PARSING이면 422, APPLYING/COMPLETED면 202 빈 body다.

PARSE/APPLY 작업이 DEAD면 getSalesImport는 500이다(D07). 클라이언트는 500을 받으면 폴링을 멈추고 실패 화면을 보여줘야 한다.

## 레시피 생성 상태

`store.generation_status`는 GENERATING/READY다. createStore 직후는 GENERATING이고 아직 작업은 없다. 판매 반영이 끝날 때 레시피가 없는 메뉴가 있으면 GENERATING으로 두고 GENERATE_RECIPES를 등록한다. 없으면 READY로 바꾼다. 작업이 성공하면 재료·레시피 초안을 한 트랜잭션에 저장하고 READY로 바꾼다. 이미 레시피가 있는 메뉴는 다시 만들지 않는다.

GENERATING 동안 setCoreIngredients와 saveRecipes는 422다. 작업이 DEAD면 candidates/recipes 조회는 500이다. 후보를 0개 만들어 READY로 두지 않는다.

## 영수증 상태

| 현재 | 트리거 | 다음 | 부작용 |
|---|---|---|---|
| 없음 | createReceiptScan | PROCESSING | images + SCAN_RECEIPTS 작업 |
| PROCESSING | 읽기 완료 | NEEDS_REVIEW | 읽은 결과와 질문을 `result`에 저장. 입고 없음 |
| NEEDS_REVIEW | confirmReceiptScan | CONFIRMED | 거래처·재료·receipt_line·답변을 한 번에 반영 |
| CONFIRMED | 같은 답변으로 재요청 | CONFIRMED | 추가 입고 없음, 204 |

읽지 못한 사진이나 줄은 결과에서 빼고 `removedDuplicateTexts`에 안내를 넣는다(D23). 한 장도 읽지 못했으면 `groups=[]`인 NEEDS_REVIEW가 된다. 외부 호출 자체가 3번 실패하면 DEAD이고 조회는 500이다. PROCESSING에서 confirm은 422다.

## 스케줄

모든 시각은 KST다. 작업 루프가 1분마다 아래 표를 확인해 해당 시각이 지난 slot의 작업을 등록한다. dedupe_key가 같으면 다시 등록되지 않으므로 여러 번 확인해도 한 번만 실행된다. `DEMO_NOW`가 설정돼 있으면 주기 스케줄러는 등록을 건너뛴다. 시연에서 지정한 slot의 1회 등록은 [실행 환경](runtime.md)을 따른다.

| 시각 | 대상 가게 | 작업 |
|---|---|---|
| 19:50 | 좌표와 메뉴가 있는 가게 | 내일 REFRESH_CONTEXT(NIGHT) → 끝나면 GENERATE_FORECAST(NIGHT) |
| 20:00 | push_token이 있고 내일이 영업일인 가게 | 내일 SEND_PUSH(NIGHT) |
| 07:20 | 좌표와 메뉴가 있는 가게 | 오늘 REFRESH_CONTEXT(MORNING) → 끝나면 GENERATE_FORECAST(MORNING) |
| 07:30 | push_token이 있고 오늘이 영업일인 가게 | 오늘 SEND_PUSH(MORNING) |

- SEND_PUSH는 같은 가게·대상일·slot의 GENERATE_FORECAST가 SUCCEEDED일 때 그 작업의 푸시 문구를 보낸다. 이전 slot 예측만 있거나 해당 작업이 진행 중이면 1분 뒤로 최대 10회 미룬다. DEAD·문구 없음·대기 만료면 발송 없이 끝낸다. 지난 slot의 알림을 나중에 보내지 않는다.
- REFRESH_CONTEXT는 날씨와 행사를 받아 weather, event에 저장한다. MORNING에서는 같은 대상일 NIGHT 예측 작업의 `nightReference.weatherText`와 새 문구를 C02대로 비교한다. DATA_CHANGED에서 새 날씨 문구가 달라지면 기존 change_text는 지운다. 외부 호출이 실패하면 기존 행을 그대로 두고 GENERATE_FORECAST는 그대로 등록한다.
- GENERATE_FORECAST는 대상일의 모든 메뉴 예측을 다시 계산해 upsert한다(C02). `adjusted`, `produced`는 건드리지 않는다. 첫 예측 행이 생기면 같은 UoW에서 `StoreWriter.markFirstForecast`를 호출한다. NIGHT·MORNING의 기준값과 푸시 문구는 [작업 payload 계약](../02-boundary-contracts.md)에 따라 기록한다.
- 가게 생성 때 GEOCODE_STORE를 등록하고, 좌표가 저장되면 오늘·내일 REFRESH_CONTEXT를 등록한다.

## 수용 조건

- 202 직후 서버를 재시작해도 작업이 끝까지 실행된다.
- 같은 작업을 두 번 실행해도 판매·입고가 두 번 반영되지 않는다.
- 반영 commit 직전에 프로세스를 끝내도 상태와 업무 데이터가 어긋나지 않는다.
- 3번 실패한 작업은 DEAD가 되고 해당 조회가 500을 돌려준다.
- 같은 slot을 여러 번 확인해도 작업·푸시가 한 번만 등록된다. 휴무일에는 푸시를 보내지 않는다.
