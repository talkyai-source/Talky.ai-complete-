FROM debian:bookworm-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN cmake -S . -B build && cmake --build build -j"$(nproc)"

FROM debian:bookworm-slim
WORKDIR /app
COPY --from=build /app/build/voice_gateway /usr/local/bin/voice_gateway

ENTRYPOINT ["/usr/local/bin/voice_gateway"]
