# whisper.cpp HTTP server — OpenAI Whisper base.en (CPU).
# Built from source so linux/arm64 (prod t4g / Apple Silicon) works; GHCR :main is amd64-only.
ARG WHISPER_CPP_REF=v1.9.5

FROM ubuntu:22.04 AS build
ARG WHISPER_CPP_REF=v1.9.5
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 --branch "${WHISPER_CPP_REF}" https://github.com/ggml-org/whisper.cpp.git .
# GGML_NATIVE=OFF: portable CPU binary for Neoverse-N1 / general aarch64.
RUN cmake -B build -DGGML_NATIVE=OFF \
 && cmake --build build -j"$(nproc)" --target whisper-server

FROM ubuntu:22.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl ffmpeg wget \
 && rm -rf /var/lib/apt/lists/*
# Server binary + shared libs (libwhisper / libggml) from the same build/bin tree.
COPY --from=build /src/build/bin/ /usr/local/lib/whisper/
RUN ln -sf /usr/local/lib/whisper/whisper-server /usr/local/bin/whisper-server \
 && ldconfig /usr/local/lib/whisper
ENV LD_LIBRARY_PATH=/usr/local/lib/whisper
COPY --from=build /src/models/download-ggml-model.sh /usr/local/bin/download-ggml-model.sh
RUN chmod +x /usr/local/bin/whisper-server /usr/local/bin/download-ggml-model.sh
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=20 \
  CMD curl -fsS http://127.0.0.1:8080/ || exit 1
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
