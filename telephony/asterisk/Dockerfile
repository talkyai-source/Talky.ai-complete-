FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       asterisk \
       ca-certificates \
       procps \
       iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY conf/modules.conf /etc/asterisk/modules.conf
COPY conf/pjsip.conf /etc/asterisk/pjsip.conf
COPY conf/extensions.conf /etc/asterisk/extensions.conf
COPY conf/http.conf /etc/asterisk/http.conf
COPY conf/ari.conf /etc/asterisk/ari.conf
COPY conf/rtp.conf /etc/asterisk/rtp.conf

EXPOSE 5070/udp 5070/tcp

CMD ["asterisk", "-f", "-U", "asterisk", "-G", "asterisk"]
