# energy-gui 1.1.6 — 1.1.5 파생 패치 이미지
#
# 2.2.1 howto 가 쓰는 energy-gui:1.1.5 는 이 저장소에 소스가 없다(컨테이너에 직접 써 넣은
# 설정으로 빌드된 이미지). 그래서 공개 이미지를 베이스로 두 가지만 바꾼다.
#
#   1) nginx resolver: 공개 DNS(1.1.1.1 8.8.8.8) 하드코딩 → 컨테이너 /etc/resolv.conf 의 nameserver
#      (proxy_pass 변수의 호스트명은 resolv.conf 가 아니라 resolver 지시자로 풀리므로,
#       Runway 도메인이 공개 DNS 에 없는 환경에서는 Host not found → 502 가 났다)
#   2) /entrypoint.sh: resolv.conf 에서 NGINX_LOCAL_RESOLVERS 를 만들어 envsubst 에 포함
#
# 화면(JS)·프록시 동작·자격 증명 처리는 1.1.5 와 같다. 1.2.x 는 gui-assets 본체에서 빌드한다.
#
#   docker buildx build --platform linux/amd64 -t <registry>/energy-gui:1.1.6 --push .
FROM gitea.try.mrxrunway.ai/tutorial-mrx/energy-gui:1.1.5
RUN sed -i 's/^resolver .*/resolver ${NGINX_LOCAL_RESOLVERS} valid=30s ipv6=off;/' /etc/nginx/conf.d/default.conf.template \
 && grep -q 'NGINX_LOCAL_RESOLVERS' /etc/nginx/conf.d/default.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
