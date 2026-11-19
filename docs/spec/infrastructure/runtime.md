# 실행 환경과 운영 조건

## 설정

기존 `DATABASE_URL`, `CORS_ALLOWED_ORIGINS`, `GIT_SHA`를 유지한다. 숫자 기본값(전환 시각, 계수, 한도 등)은 환경변수가 아니라 [정책 파일](../policy-defaults.toml)에 둔다.

| 환경변수 | 필수 | 내용 |
|---|---|---|
| `DATABASE_URL` | 필수 | PostgreSQL 연결 문자열 |
| `CORS_ALLOWED_ORIGINS` | 선택 | 쉼표로 구분한 origin. PWA 주소를 넣는다 |
| `GIT_SHA` | 선택 | `/health`의 version. 기본 `dev` |
| `FILE_STORAGE_ROOT` | 필수 | 업로드 파일을 둘 절대 경로. 컨테이너를 다시 띄워도 남는 볼륨이어야 한다 |
| `ANTHROPIC_API_KEY` | 필수 | Claude API 키 |
| `ANTHROPIC_MODEL` | 선택 | 기본 `claude-opus-5-5` |
| `KAKAO_REST_API_KEY` | 필수 | 카카오 로컬 API |
| `KMA_SERVICE_KEY` | 필수 | 기상청 단기예보(공공데이터포털) |
| `SEOUL_OPENAPI_KEY` | 필수 | 서울 열린데이터광장(문화행사, 실시간 도시데이터) |
| `SEOUL_CITYDATA_KEY` | 선택 | 실시간 도시데이터에 별도 키가 필요할 때만 |
| `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT` | 필수 | 웹 푸시. subject는 `mailto:` 주소 |
| `DEMO_NOW` | 선택 | offset이 붙은 ISO 8601 시각(예: `2026-11-10T20:30:00+09:00`). 설정하면 Clock이 항상 이 시각을 돌려주고 스케줄 작업을 등록하지 않는다(D27) |

필수 값이 없으면 서버 시작 때 실패한다. 테스트는 Clock과 외부 포트를 fake로 바꾸므로 외부 키 없이 돈다. Clock의 시간대는 코드에서 `Asia/Seoul`로 고정한다.

`DEMO_NOW` 사용법: 값을 `…T20:30:00+09:00`으로 두고 서버를 띄우면 전날 밤 화면(TOMORROW), 다음 날 `…T07:40:00+09:00`으로 바꿔 다시 띄우면 당일 아침 화면(TODAY)이 나온다. 스케줄이 돌지 않으므로 시연 전에 예측을 만들어 두려면 판매 반영(예측 작업이 자동 등록된다)을 그 시각 설정에서 한 번 실행한다. 운영 환경에는 설정하지 않는다.

## 배포

- 프로세스는 지금처럼 uvicorn 1개다(포트 8000, ARM64 이미지). 작업 루프는 FastAPI lifespan에서 시작하고 종료 신호를 받으면 새 작업을 꺼내지 않고 멈춘다.
- uvicorn의 `--workers`를 2 이상으로 올리지 않는다. 작업 루프가 프로세스마다 떠서 같은 작업을 두 번 실행하게 된다.
- Dockerfile은 지금 `app`과 `openapi.yaml`만 복사한다. 구현 때 Alembic 설정과 migration 폴더도 복사하도록 고친다. 정책 파일은 `app/policies/` 아래에 두므로 `app` 복사에 포함된다.
- 배포 서버에서 바뀌는 것은 세 가지다: 위 환경변수 추가, `FILE_STORAGE_ROOT`에 볼륨 연결, 컨테이너 시작 전에 `alembic upgrade head` 실행. 서버의 배포 스크립트는 이 저장소에 없으므로 이 세 가지를 배포 담당자가 반영해야 한다.
- `/health`는 지금처럼 `status`, `version`을 돌려준다. DB 연결 확인을 `/health`에 넣지 않는다(배포 스크립트가 기동 확인에 쓴다).

DB 연결 수는 API 요청용 pool 5 + overflow 5로 시작한다. 작업 루프는 같은 pool을 쓴다. 외부 서비스를 부르는 동안 DB 세션을 열어 두지 않는다.

## 로그

- 요청: requestId, operationId, storeId, status, 걸린 시간.
- 작업: jobId, kind, attempts, state, 걸린 시간, 실패 사유.
- 외부 호출: 공급자, 걸린 시간, 재시도 횟수.

DEAD 작업, 오래 GENERATING/PARSING/PROCESSING에 머무는 가게, 500 응답은 로그에서 찾을 수 있어야 한다.

## 완료의 구분

MVP 완료는 25개 operation, 재시작해도 이어지는 비동기 작업, 계산 규칙, 실제 외부 연동, 계약 테스트, 기존 배포 경로가 동작하는 상태다. 사용자 인증, 판매의 지속 수집, 재고 실사, 단위·유통기한 정정 화면, 실패 재시도 API는 범위 밖이다([공개 계약이 표현하지 못하는 것](../00-scope-and-decisions.md)).
