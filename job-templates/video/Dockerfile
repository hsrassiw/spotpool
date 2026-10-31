FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /input /output \
    && chown 65534:65534 /output

ENV SPOTPOOL_VIDEO_HEIGHT=720

WORKDIR /tmp
USER 65534:65534

# Worker passes an input path and output path; the environment carries the optional height.
ENTRYPOINT ["sh", "-c", "if [ \"$#\" -ne 2 ]; then echo 'Usage: spotpool-video INPUT OUTPUT' >&2; exit 2; fi; case \"$SPOTPOOL_VIDEO_HEIGHT\" in ''|*[!0-9]*) echo 'SPOTPOOL_VIDEO_HEIGHT must be an integer' >&2; exit 2 ;; esac; if [ \"$SPOTPOOL_VIDEO_HEIGHT\" -lt 2 ] || [ \"$SPOTPOOL_VIDEO_HEIGHT\" -gt 4320 ] || [ \"$((SPOTPOOL_VIDEO_HEIGHT % 2))\" -ne 0 ]; then echo 'SPOTPOOL_VIDEO_HEIGHT must be an even integer between 2 and 4320' >&2; exit 2; fi; exec ffmpeg -nostdin -hide_banner -y -i \"$1\" -vf \"scale=-2:$SPOTPOOL_VIDEO_HEIGHT\" -c:v libx264 -preset medium -crf 23 -c:a aac -b:a 128k -movflags +faststart -f mp4 \"$2\"", "spotpool-video"]
