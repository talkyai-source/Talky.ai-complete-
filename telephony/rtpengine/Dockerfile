FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# SECURITY: Pin RTPEngine version to >= mr13.4.1.1 to mitigate CVE-2025-53399
# CVE-2025-53399: RTP Injection and Media Bleed vulnerabilities (CVSS 9.3)
# Versions <= mr13.3.1.4 are vulnerable to media hijacking attacks
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       iproute2 \
       rtpengine-daemon \
    && rm -rf /var/lib/apt/lists/* \
    && rtpengine --version | grep -E "mr13\.[4-9]|mr1[4-9]\.|mr[2-9][0-9]\." || \
       (echo "ERROR: RTPEngine version must be >= mr13.4.1.1 for CVE-2025-53399 mitigation" && exit 1)

EXPOSE 2223/udp
EXPOSE 30000-34999/udp

CMD ["rtpengine", "--config-file=/etc/rtpengine/rtpengine.conf", "--foreground", "--log-level=6"]
