#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
    echo 'Usage: spotpool-video INPUT OUTPUT' >&2
    exit 2
fi
height=${SPOTPOOL_VIDEO_HEIGHT-720}
case "$height" in
    ''|*[!0-9]*) echo 'SPOTPOOL_VIDEO_HEIGHT must be an integer' >&2; exit 2 ;;
esac
# Normalize leading zeros before POSIX shell arithmetic (which may interpret octal).
while [ "${height#0}" != "$height" ] && [ "$height" != '0' ]; do
    height=${height#0}
done
if [ "${#height}" -gt 4 ] || [ "$height" -lt 2 ] || [ "$height" -gt 4320 ] || [ "$((height % 2))" -ne 0 ]; then
    echo 'SPOTPOOL_VIDEO_HEIGHT must be an even integer between 2 and 4320' >&2
    exit 2
fi
exec ffmpeg -nostdin -hide_banner -y -i "$1" \
    -vf "scale=-2:$height" -c:v libx264 -preset medium -crf 23 -pix_fmt yuv420p \
    -c:a aac -b:a 128k -movflags +faststart -f mp4 "$2"
