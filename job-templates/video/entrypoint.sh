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
segment=${SPOTPOOL_VIDEO_SEGMENT_SECONDS-20}
case "$segment" in
    ''|*[!0-9]*) echo 'SPOTPOOL_VIDEO_SEGMENT_SECONDS must be an integer' >&2; exit 2 ;;
esac
while [ "${segment#0}" != "$segment" ] && [ "$segment" != '0' ]; do
    segment=${segment#0}
done
if [ "${#segment}" -gt 4 ] || [ "$segment" -lt 1 ] || [ "$segment" -gt 3600 ]; then
    echo 'SPOTPOOL_VIDEO_SEGMENT_SECONDS must be between 1 and 3600' >&2
    exit 2
fi

# Container chỉ được --cpus vCPU và tối đa 64 tiến trình, nhưng ffmpeg thấy mọi nhân của máy chủ và
# tạo luồng theo số đó; máy nhiều nhân sẽ vượt giới hạn. Giữ 2 luồng cho giải mã, bộ lọc và x264.
threads=2

encode_whole() {
    exec ffmpeg -nostdin -hide_banner -y -filter_threads "$threads" -threads "$threads" -i "$1" \
        -threads "$threads" -vf "scale=-2:$height" -c:v libx264 -preset medium -crf 23 -pix_fmt yuv420p \
        -c:a aac -b:a 128k -movflags +faststart -f mp4 "$2"
}

# Không có thư mục checkpoint (chạy ngoài worker): nén một lượt như trước.
checkpoint=${SPOTPOOL_CHECKPOINT_DIR-}
if [ -z "$checkpoint" ] || [ ! -d "$checkpoint" ]; then
    encode_whole "$1" "$2"
fi

# Có checkpoint: chia phần hình thành các đoạn, nén xong đoạn nào lưu đoạn đó vào thư mục
# checkpoint. Bị thu hồi thì lượt sau bỏ qua các đoạn đã có. Âm thanh nén một lần lúc ghép.
info=$(ffmpeg -nostdin -hide_banner -i "$1" 2>&1) || true
seconds=$(printf '%s\n' "$info" | sed -n 's/^ *Duration: \([0-9][0-9]*\):\([0-9][0-9]\):\([0-9][0-9.]*\),.*/\1 \2 \3/p' \
    | head -n 1 | awk 'NF == 3 { printf "%d", $1 * 3600 + $2 * 60 + $3 }')
has_video=$(printf '%s\n' "$info" | grep 'Stream #' | grep 'Video:' | grep -v 'attached pic' | head -n 1) || true
# Không đọc được thời lượng, không có hình, hoặc ngắn hơn hai đoạn: chia đoạn không có lợi.
if [ -z "$seconds" ] || [ -z "$has_video" ] || [ "$seconds" -lt $((segment * 2)) ]; then
    encode_whole "$1" "$2"
fi
# Đoạn cuối gộp phần lẻ, nên không có đoạn nào quá ngắn hoặc rỗng.
count=$((seconds / segment))
size=$(wc -c < "$1" | tr -d ' ')
meta="spotpool-video v1 height=$height segment=$segment seconds=$seconds size=$size"
if [ "$(cat "$checkpoint/video.meta" 2>/dev/null || true)" != "$meta" ]; then
    # Checkpoint của lần chạy khác tham số (hoặc chưa có): bỏ hết, nén từ đầu.
    rm -f "$checkpoint"/seg_*.mp4 "$checkpoint"/*.tmp
    printf '%s\n' "$meta" > "$checkpoint/video.meta.tmp"
    mv "$checkpoint/video.meta.tmp" "$checkpoint/video.meta"
fi

index=0
while [ "$index" -lt "$count" ]; do
    [ -f "$checkpoint/$(printf 'seg_%04d.mp4' "$index")" ] || break
    index=$((index + 1))
done
done_count=$index
if [ "$done_count" -gt 0 ]; then
    echo "Tiếp tục từ đoạn $((done_count + 1))/$count (đã nén xong $done_count đoạn, mỗi đoạn $segment giây)."
else
    echo "Chia video $seconds giây thành $count đoạn, mỗi đoạn $segment giây."
fi

index=$done_count
while [ "$index" -lt "$count" ]; do
    name=$(printf 'seg_%04d.mp4' "$index")
    start=$((index * segment))
    if [ "$index" -eq $((count - 1)) ]; then
        limit=''
    else
        limit="-t $segment"
    fi
    # Ghi ra *.tmp rồi đổi tên: nền tảng không lưu file *.tmp nên không có đoạn nén dở.
    # shellcheck disable=SC2086
    ffmpeg -nostdin -hide_banner -loglevel error -y -filter_threads "$threads" -threads "$threads" \
        -ss "$start" -i "$1" $limit -threads "$threads" -map 0:v:0 -an -sn -dn -vf "scale=-2:$height" \
        -c:v libx264 -preset medium -crf 23 -pix_fmt yuv420p -f mp4 "$checkpoint/$name.tmp"
    mv "$checkpoint/$name.tmp" "$checkpoint/$name"
    index=$((index + 1))
    echo "Xong đoạn $index/$count."
done

list="$checkpoint/concat.tmp"
: > "$list"
index=0
while [ "$index" -lt "$count" ]; do
    printf "file '%s'\n" "$(printf 'seg_%04d.mp4' "$index")" >> "$list"
    index=$((index + 1))
done
echo "Ghép $count đoạn và nén âm thanh."
ffmpeg -nostdin -hide_banner -loglevel error -y -f concat -safe 0 -i "$list" -threads "$threads" -i "$1" \
    -threads "$threads" -map 0:v:0 -map '1:a:0?' -c:v copy -c:a aac -b:a 128k -movflags +faststart -f mp4 "$2"
rm -f "$list"
