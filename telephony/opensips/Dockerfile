FROM opensips/opensips:3.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       opensips-tls-module \
       opensips-tlsmgm-module \
       opensips-tls-openssl-module \
    && rm -rf /var/lib/apt/lists/*
