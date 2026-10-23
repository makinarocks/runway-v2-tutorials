#!/bin/sh
# Agent Injector 가 마운트한 /vault/secrets/creds.env 로부터 env 로드 →
# nginx.conf.template 의 ${VAR} 치환 → nginx 시작
#
# 1.1.5 의 /entrypoint.sh 에 NGINX_LOCAL_RESOLVERS 계산만 추가한 것이다.
set -e

if [ -f /vault/secrets/creds.env ]; then
    echo "[entrypoint] Loading secrets from /vault/secrets/creds.env"
    . /vault/secrets/creds.env
else
    echo "[entrypoint] /vault/secrets/creds.env 없음 — Agent Injector annotation 누락 확인 필요"
fi

# 필수 env 검증
: "${RUNWAY_BASE_DOMAIN:?RUNWAY_BASE_DOMAIN 미설정}"
: "${RUNWAY_PROJECT_ID:?RUNWAY_PROJECT_ID 미설정}"
: "${RUNWAY_API_KEY:?RUNWAY_API_KEY 미설정}"

# 1.1.6: 동적 proxy_pass 용 resolver 를 컨테이너의 /etc/resolv.conf 에서 가져온다.
# nginx 는 proxy_pass 변수의 호스트명을 resolv.conf 가 아니라 resolver 지시자로 푼다.
# (nginx 공식 이미지 15-local-resolvers.envsh 와 같은 방식)
NGINX_LOCAL_RESOLVERS=$(awk 'BEGIN{ORS=" "} $1=="nameserver" {if ($2 ~ ":") {print "["$2"]"} else {print $2}}' /etc/resolv.conf)
NGINX_LOCAL_RESOLVERS="${NGINX_LOCAL_RESOLVERS% }"
: "${NGINX_LOCAL_RESOLVERS:=1.1.1.1 8.8.8.8}"
export NGINX_LOCAL_RESOLVERS
echo "[entrypoint] resolver → ${NGINX_LOCAL_RESOLVERS}"

echo "[entrypoint] env substitute → /etc/nginx/conf.d/default.conf"
envsubst '${RUNWAY_BASE_DOMAIN} ${RUNWAY_PROJECT_ID} ${RUNWAY_API_KEY} ${NGINX_LOCAL_RESOLVERS}' \
    < /etc/nginx/conf.d/default.conf.template \
    > /etc/nginx/conf.d/default.conf

echo "[entrypoint] nginx 시작"
exec nginx -g 'daemon off;'
