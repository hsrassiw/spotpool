#!/bin/bash
# Đo gói Ổn định khi cụm 0 máy và warm pool có 1 máy đã tắt: nộp job mẫu, đo thời gian chờ,
# rồi đặt desired về 0 để máy quay lại warm pool ngay (không đợi target tracking khoảng 15 phút).
# Dùng: SPOTPOOL_API=... bash warm_measure.sh [số lần, mặc định 5]
cd "$(dirname "$0")"
export AWS_DEFAULT_REGION=ap-southeast-1
ASG=spotpool-worker-ondemand
N=${1:-5}
PY=${PY:-python}

ready() {  # cụm 0 máy, warm pool có 1 máy đã tắt
  local d w
  d=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $ASG \
      --query "[AutoScalingGroups[0].DesiredCapacity, length(AutoScalingGroups[0].Instances)]" --output text | awk '{print $1+$2}')
  w=$(aws autoscaling describe-warm-pool --auto-scaling-group-name $ASG \
      --query "Instances[?LifecycleState=='Warmed:Stopped'] | length(@)" --output text)
  [ "$d" = "0" ] && [ "$w" = "1" ]
}

for i in $(seq 1 "$N"); do
  ok=0
  for _ in $(seq 1 60); do
    if ready; then ok=1; break; fi
    sleep 10
  done
  [ "$ok" = 1 ] || { echo "$(date +%T) chưa sẵn sàng sau 10 phút, dừng"; exit 1; }
  echo "$(date +%T) lần $i: cụm 0 máy, warm pool có 1 máy đã tắt"
  "$PY" run.py sample ondemand 2>&1 | grep -E "job |Error|Traceback"
  aws autoscaling set-desired-capacity --auto-scaling-group-name $ASG --desired-capacity 0
  echo "$(date +%T) đặt desired = 0"
  sleep 30
done
echo "xong $(date +%T)"
