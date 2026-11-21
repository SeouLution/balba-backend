#!/usr/bin/env bash
# balba 배포 스크립트 — 서버 위치: /opt/balba/deploy.sh
#
#   사용법: deploy.sh <backend|frontend> <image-tag>
#
# GitHub Actions가 SSH로 호출한다. 배포 키는 authorized_keys 의 강제 명령으로
# 이 스크립트만 실행할 수 있다.
#
# 동작: 해당 서비스 이미지만 pull → 그 서비스만 재기동 → 헬스체크
#       실패하면 이전 태그로 되돌리고 exit 1.
# 롤백: deploy.sh <service> <이전 태그> 를 다시 실행하면 된다.
set -euo pipefail

APP_ROOT=/opt/balba
IMAGES_ENV="$APP_ROOT/.images.env"
HEALTH_RETRIES=30
HEALTH_INTERVAL=5

service="${1:-}"
tag="${2:-}"

case "$service" in
  backend)  tag_var=BACKEND_TAG;  health_url="http://backend:8000${BACKEND_HEALTH_PATH:-/health}" ;;
  frontend) tag_var=FRONTEND_TAG; health_url="http://frontend:80/" ;;
  *) echo "usage: deploy.sh <backend|frontend> <image-tag>" >&2; exit 2 ;;
esac

if [[ ! "$tag" =~ ^[A-Za-z0-9_.-]{1,128}$ ]]; then
  echo "invalid image tag: '$tag'" >&2
  exit 2
fi

cd "$APP_ROOT"

# 두 레포가 같은 서버에 배포하므로 동시에 실행되지 않게 잠근다.
exec 9>"$APP_ROOT/.deploy.lock"
flock -w 600 9 || { echo "다른 배포가 10분 넘게 진행 중 — 중단" >&2; exit 1; }

compose() {
  docker compose --env-file .env.production --env-file "$IMAGES_ENV" "$@"
}

set_tag() {
  sed -i "s|^${tag_var}=.*|${tag_var}=$1|" "$IMAGES_ENV"
}

healthy() {
  # 앱 이미지에 curl 이 없어도 되도록 caddy 컨테이너에서 내부 네트워크로 확인한다.
  for _ in $(seq 1 "$HEALTH_RETRIES"); do
    if compose exec -T caddy wget -q -O /dev/null -T 5 "$health_url" 2>/dev/null; then
      return 0
    fi
    sleep "$HEALTH_INTERVAL"
  done
  return 1
}

prev_tag="$(grep "^${tag_var}=" "$IMAGES_ENV" | cut -d= -f2)"
echo "── $service: $prev_tag → $tag"

set_tag "$tag"
if ! compose pull "$service"; then
  echo "❌ 이미지 pull 실패 — 태그를 $prev_tag 로 되돌림" >&2
  set_tag "$prev_tag"
  exit 1
fi

# 새 이미지로 DB 스키마를 먼저 올린다 (README: 컨테이너 시작 전에 alembic upgrade head).
if [ "$service" = backend ]; then
  echo "── DB 마이그레이션 (alembic upgrade head)"
  if ! compose run --rm --no-deps -T backend alembic upgrade head; then
    echo "❌ 마이그레이션 실패 — 태그를 $prev_tag 로 되돌림" >&2
    set_tag "$prev_tag"
    exit 1
  fi
fi

compose up -d --no-deps "$service"

if healthy; then
  echo "✅ $service 배포 완료 ($tag)"
  docker image prune -f >/dev/null 2>&1 || true
  exit 0
fi

echo "❌ 헬스체크 실패 ($health_url) — 최근 로그:" >&2
compose logs --tail=50 "$service" >&2 || true

echo "── 이전 태그($prev_tag)로 되돌리는 중" >&2
set_tag "$prev_tag"
compose up -d --no-deps "$service" >&2 || echo "⚠️ 되돌리기 실패 — 수동 확인 필요" >&2
exit 1
