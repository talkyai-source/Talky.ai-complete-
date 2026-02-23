FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       iproute2 \
       rtpengine-daemon \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 2223/udp
EXPOSE 30000-30100/udp

CMD ["rtpengine", "--config-file=/etc/rtpengine/rtpengine.conf", "--foreground", "--log-level=6"]
