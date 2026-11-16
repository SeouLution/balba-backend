# balba-backend

발주바주 백엔드 (FastAPI).

## 로컬 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload      # http://localhost:8000/health
pytest -q
ruff check .
```

## 배포

`main`에 푸시하면 GitHub Actions가 테스트 → ARM64 이미지 빌드(GHCR) → 서버 배포까지 자동으로 진행한다.
운영 주소: https://api.balba.duckdns.org

배포가 동작하려면 아래 약속을 지켜야 한다.

- 컨테이너는 **8000 포트**로 뜬다.
- **`GET /health`** 가 200을 반환하고, 응답에 `GIT_SHA` 값이 포함된다 (배포 확인에 사용).
- 설정은 환경변수로 받는다. 서버가 주입하는 값: `DATABASE_URL`, `CORS_ALLOWED_ORIGINS`.

PR에서는 lint와 테스트만 실행된다.
