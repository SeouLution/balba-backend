# 운영 서버가 ARM64(OCI Ampere)라서 CI가 linux/arm64 로 빌드한다.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY openapi.yaml alembic.ini ./

# 업로드 폴더를 app 사용자 소유로 만들어 둔다. 이 경로에 named volume을 연결하면 소유권이 그대로 따라온다.
RUN useradd --system --no-create-home app \
    && install -d -o app -g app /var/lib/balba/uploads
USER app

# /health 가 배포된 커밋을 알려주도록 빌드 시점에 주입한다.
ARG GIT_SHA=dev
ENV GIT_SHA=${GIT_SHA}

EXPOSE 8000
# 작업 루프가 프로세스마다 뜨므로 --workers 를 2 이상으로 올리지 않는다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
