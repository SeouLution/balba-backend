# 실행 환경과 운영 조건

## 설정

기존 `DATABASE_URL`, `CORS_ALLOWED_ORIGINS`, `GIT_SHA`를 유지한다. 숫자 기본값(전환 시각, 계수, 한도 등)은 환경변수가 아니라 [정책 파일](../../../app/policies/defaults.toml)에 둔다.

| 환경변수 | 필수 | 내용 |
|---|---|---|
| `DATABASE_URL` | 필수 | PostgreSQL 연결 문자열 |
| `CORS_ALLOWED_ORIGINS` | 선택 | 쉼표로 구분한 origin. PWA 주소를 넣는다 |
| `GIT_SHA` | 선택 | `/health`의 version. 기본 `dev` |
| `FILE_STORAGE_ROOT` | 필수 | 업로드 파일을 둘 절대 경로. 컨테이너를 다시 띄워도 남는 볼륨이어야 한다 |
| `ANTHROPIC_API_KEY` | 필수 | Claude API 키 |
| `ANTHROPIC_MODEL` | 선택 | 기본 `claude-opus-5-5` |
| `KAKAO_REST_API_KEY` | 필수 | 카카오 로컬 API |
| `KMA_SERVICE_KEY` | 필수 | 기상청 단기예보와 지상(종관, ASOS) 일자료(공공데이터포털). 두 서비스 모두 활용신청이 필요하다 |
| `SEOUL_OPENAPI_KEY` | 필수 | 서울 열린데이터광장(문화행사, 실시간 도시데이터) |
| `SEOUL_CITYDATA_KEY` | 선택 | 실시간 도시데이터에 별도 키가 필요할 때만 |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT` | 필수 | 웹 푸시. subject는 `mailto:` 주소 |
| `DEMO_NOW` | 선택 | offset이 붙은 ISO 8601 시각(예: `2026-11-10T20:30:00+09:00`). 설정하면 Clock이 항상 이 시각을 돌려주고 주기 스케줄러를 멈춘다(D27) |

필수 값이 없으면 서버 시작 때 실패한다. 테스트는 Clock과 외부 포트를 fake로 바꾸므로 외부 키 없이 돈다. Clock의 시간대는 코드에서 `Asia/Seoul`로 고정한다.

`DEMO_NOW` 사용법: 값을 `…T20:30:00+09:00`으로 두고 서버를 띄우면 전날 밤 화면(TOMORROW), 다음 날 `…T07:40:00+09:00`으로 바꿔 다시 띄우면 당일 아침 화면(TODAY)이 나온다. 밤과 아침을 비교하는 시연은 내부 작업 등록 경로를 1회씩 호출한다. 온보딩과 좌표 준비 후 밤 시각에서 내일 날짜의 REFRESH_CONTEXT(NIGHT)를 등록하고 같은 slot의 GENERATE_FORECAST 성공을 확인한다. 날짜를 넘겨 재시작한 뒤 같은 대상일의 REFRESH_CONTEXT(MORNING)를 등록하고 아침 예측 성공 후 홈을 조회한다. 두 번 모두 운영 handler와 dedupe_key를 재사용한다. 시연용 1회 실행은 가게·대상일·slot을 지정하는 관리 절차로 두며 공개 HTTP 계약을 추가하지 않는다. 운영 환경에는 설정하지 않는다.

## 배포

- 프로세스는 지금처럼 uvicorn 1개다(포트 8000, ARM64 이미지). 작업 루프는 FastAPI lifespan에서 시작하고 종료 신호를 받으면 새 작업을 꺼내지 않고 멈춘다.
- uvicorn의 `--workers`를 2 이상으로 올리지 않는다. 작업 루프가 프로세스마다 떠서 같은 작업을 두 번 실행하게 된다.
- Dockerfile은 지금 `app`과 `openapi.yaml`만 복사한다. 구현 때 Alembic 설정과 migration 폴더도 복사하도록 고친다. 정책 파일은 `app/policies/` 아래에 두므로 `app` 복사에 포함된다.
- 배포 서버에서 바뀌는 것은 세 가지다: 위 환경변수 추가, `FILE_STORAGE_ROOT`에 볼륨 연결, 컨테이너 시작 전에 `alembic upgrade head` 실행. 서버의 배포 스크립트는 이 저장소에 없으므로 이 세 가지를 배포 담당자가 반영해야 한다.
- `/health`는 지금처럼 `status`, `version`을 돌려준다. DB 연결 확인을 `/health`에 넣지 않는다(배포 스크립트가 기동 확인에 쓴다).

`DEMO_NOW`는 영업일·홈·표시 시각에 쓰는 업무 시계다. 작업 대기·재시도 지연은 실제 경과 시간으로 처리해 고정 시각에서도 큐가 진행되게 한다.

DB 연결 수는 API 요청용 pool 5 + overflow 5로 시작한다. 작업 루프는 같은 pool을 쓴다. 외부 서비스를 부르는 동안 DB 세션을 열어 두지 않는다.

## 로그

- 요청: requestId, operationId, storeId, status, 걸린 시간.
- 작업: jobId, kind, attempts, state, 걸린 시간, 실패 사유.
- 외부 호출: 공급자, 걸린 시간, 재시도 횟수.

DEAD 작업, 오래 GENERATING/PARSING/PROCESSING에 머무는 가게, 500 응답은 로그에서 찾을 수 있어야 한다.

## 완료의 구분

MVP 완료는 가게 API 22개 operation, 재시작해도 이어지는 비동기 작업, 계산 규칙, 실제 외부 연동, 계약 테스트, 기존 배포 경로가 동작하는 상태다. 사용자 인증, 판매의 지속 수집, 재고 실사, 단위 정정 화면, 실패 재시도 API는 범위 밖이다([공개 계약이 표현하지 못하는 것](../00-scope-and-decisions.md)).
