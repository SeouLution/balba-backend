# balba-backend

발주바주 백엔드 (FastAPI).

## 로컬 실행

PostgreSQL이 필요하다. 설정은 환경변수로 받고, 필요한 이름은 .env.example 파일에 있다. 필수 값이 하나라도 없으면 서버가 뜨지 않는다.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
docker run -d --name balba_db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=balba -p 127.0.0.1:5432:5432 postgres:17-alpine
alembic upgrade head               # DATABASE_URL 을 읽는다. 서버를 띄우기 전에 한 번 실행한다
uvicorn app.main:app --reload      # http://localhost:8000/health · API 는 /v1 아래
```

## 환경변수

키와 접속 주소처럼 환경마다 다르고 저장소에 올리면 안 되는 값이다.

| 이름 | 용도 | 필수 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL 접속 주소. 서버와 마이그레이션(`alembic`)이 읽는다 | 필수 |
| `FILE_STORAGE_ROOT` | 올린 판매 파일과 영수증 사진을 저장할 폴더 경로 | 필수 |
| `ANTHROPIC_API_KEY` | Claude API 키. 엑셀 열 판별, 영수증 읽기, 레시피 초안에 쓴다 | 필수 |
| `ANTHROPIC_MODEL` | 쓸 Claude 모델 이름. 비우면 `claude-opus-5-5` | 선택 |
| `KAKAO_REST_API_KEY` | 가게 주소를 좌표로 바꾸는 카카오 주소 검색 키. 카카오 앱에서 카카오맵 사용을 켜야 한다 | 필수 |
| `KMA_SERVICE_KEY` | 기상청 단기예보 키(공공데이터포털). 날씨 타일과 날씨 보정에 쓴다 | 필수 |
| `SEOUL_OPENAPI_KEY` | 서울 열린데이터광장 키. 근처 행사와 행사장 혼잡도에 쓴다 | 필수 |
| `SEOUL_CITYDATA_KEY` | 실시간 도시데이터에 별도 키가 필요할 때만. 비우면 위 키를 쓴다 | 선택 |
| `VAPID_PRIVATE_KEY` | 웹 푸시를 보낼 때 서명하는 개인키 | 필수 |
| `VAPID_PUBLIC_KEY` | 위 개인키와 쌍인 공개키. 서버는 값이 있는지만 확인하고, 프론트가 푸시 구독할 때 쓴다 | 필수 |
| `VAPID_SUBJECT` | 푸시 서비스에 알리는 운영자 연락처. `mailto:메일주소` 형식 | 필수 |
| `JWT_SECRET` | 로그인 쿠키의 JWT 서명 키. 충분히 긴 임의 문자열(예: `openssl rand -hex 32`). 바꾸면 모든 로그인이 풀린다 | 필수 |
| `CORS_ALLOWED_ORIGINS` | API 호출을 허용할 프론트 주소. 쉼표로 여러 개. 비우면 다른 주소의 브라우저 요청이 막힌다 | 선택 |
| `GIT_SHA` | `/health`가 돌려주는 버전 값. 배포 스크립트가 배포된 커밋을 확인하는 데 쓴다. 로컬에서는 `dev` | 선택 |
| `DEMO_NOW` | 서버의 현재 시각을 고정하는 값. 넣으면 정해진 시각의 자동 작업이 멈춘다. 시연용이며 운영에서는 비운다 | 선택 |

- `VAPID_PRIVATE_KEY`와 `VAPID_PUBLIC_KEY`를 바꾸면 기존 푸시 구독이 전부 거절된다. `VAPID_SUBJECT`는 바꿔도 된다.
- Claude API는 유료이고 콘솔에서 크레딧을 충전해야 호출된다. 나머지 키는 무료다.
- 키를 넣은 뒤 `python -m app.infrastructure.providers.check`로 공급자를 한 번씩 실제 호출해 볼 수 있다. 키 값은 출력하지 않는다.

## 정책 파일

`app/policies/defaults.toml`은 서비스 동작을 정하는 숫자와 목록을 모아 둔 설정 파일이다. 서버가 시작할 때 한 번 읽는다. 값을 바꾸려면 코드를 고치지 않고 이 파일만 고친다.

환경변수와 달리 숨길 필요가 없는 업무 규칙 값이라 저장소에 함께 둔다. 숫자는 실측 근거 없이 AI가 쓴 초안이므로 운영 전에 검토가 필요하다.

| 항목 | 내용 |
|---|---|
| 홈 전환 시각 | 밤 8시부터 '내일 준비량' 화면으로 바뀐다 |
| 예측 보정 | 날씨별 계수(비 0.90, 눈 0.85 등), 행사 계수 1.20, 행사 반경 3km, 사용할 행사 분류 목록 |
| 메뉴 단위 | 메뉴 이름에 '김밥'이 있으면 '줄', '떡볶이'면 '인분' 같은 규칙 |
| 재료 배지 기준 | 이름에 '소고기' 같은 단어가 있으면 '비쌈', 메뉴 3개 이상에 쓰이거나 '비쌈'이면 '추천' |
| 거래처 | 영수증 날짜가 3개 이상이면 주문 요일 추정 |
| 할인 | 17시부터 추천, 추천 할인 폭 30%, 마감 2시간 전 시작, 채널 목록 |
| 업로드 한도 | 판매 파일 20MB, 영수증 사진 장당 10MB 등 |

## 테스트

테스트는 실제 PostgreSQL을 쓴다. 외부 서비스는 fake로 바꾸므로 키가 필요 없다.

```bash
docker run -d --name balba_test_db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=balba_test -p 127.0.0.1:55432:5432 postgres:17-alpine
pytest -q                          # 다른 DB를 쓰려면 TEST_DATABASE_URL 을 지정한다
ruff check .
openapi-spec-validator openapi.yaml
```

## 시연용 시각 고정

`DEMO_NOW`(예: `2026-11-10T20:30:00+09:00`)를 주면 서버의 업무 시각이 그 값으로 고정되고 정해진 시각의 작업 등록이 멈춘다. 밤·아침 예측은 아래 명령으로 한 번씩 등록한다. 운영 환경에는 설정하지 않는다.

```bash
python -m app.infrastructure.jobs.schedule <storeId> 2026-11-11 NIGHT
python -m app.infrastructure.jobs.schedule <storeId> 2026-11-11 MORNING
```

## 배포

`main`에 푸시하면 GitHub Actions가 테스트 → ARM64 이미지 빌드(GHCR) → 서버 배포까지 자동으로 진행한다.
운영 주소: https://api.balba.duckdns.org

배포가 동작하려면 아래 약속을 지켜야 한다.

- 컨테이너는 **8000 포트**로 뜬다.
- **`GET /health`** 가 200을 반환하고, 응답에 `GIT_SHA` 값이 포함된다 (배포 확인에 사용).
- 설정은 환경변수로 받는다. 서버가 주입하는 값: `DATABASE_URL`, `CORS_ALLOWED_ORIGINS`, 그리고 .env.example 에 적힌 나머지 필수 값.
- 컨테이너를 시작하기 전에 같은 이미지로 `alembic upgrade head`를 한 번 실행한다.
- `FILE_STORAGE_ROOT`에는 컨테이너를 다시 띄워도 남는 볼륨을 연결한다. 이미지의 `app` 사용자가 쓸 수 있어야 한다.
- uvicorn 프로세스는 1개로 둔다. 비동기 작업 루프가 같은 프로세스 안에서 돈다.

PR에서는 lint와 테스트만 실행된다.
