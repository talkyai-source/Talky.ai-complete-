FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       gnupg2 \
       iproute2 \
       lsb-release \
       procps \
       wget \
    && curl -fsSL https://packagecloud.io/install/repositories/freeswitch/release/script.deb.sh | bash \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       freeswitch-meta-vanilla \
       freeswitch-mod-event-socket \
       freeswitch-mod-sofia \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 8021/tcp
EXPOSE 5080/tcp
EXPOSE 5080/udp

CMD ["freeswitch", "-nonat", "-nf", "-ncwait"]
