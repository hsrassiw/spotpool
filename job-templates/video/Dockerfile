FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /input /output \
    && chown 65534:65534 /output

ENV SPOTPOOL_VIDEO_HEIGHT=720
COPY --chmod=755 entrypoint.sh /usr/local/bin/spotpool-video

WORKDIR /tmp
USER 65534:65534

# Worker truyền đường dẫn đầu vào/đầu ra và chiều cao tùy chọn qua biến môi trường; đầu ra là một tệp.
ENTRYPOINT ["/usr/local/bin/spotpool-video"]
