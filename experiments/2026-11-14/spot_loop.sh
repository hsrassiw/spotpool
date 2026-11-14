#!/bin/bash
# Cần SPOTPOOL_API như run.py. Mỗi khi cụm Spot về 0 máy thì nộp 1 job nhỏ gói Tiết kiệm và đo thời gian chờ. Dừng lúc 07:30.
cd "$(dirname "$0")"
end=$(date -d "today 07:30" +%s)
while [ "$(date +%s)" -lt "$end" ]; do
  n=$(aws autoscaling describe-auto-scaling-groups --region ap-southeast-1 --auto-scaling-group-names spotpool-worker-spot \
      --query "[AutoScalingGroups[0].DesiredCapacity, length(AutoScalingGroups[0].Instances)]" --output text | awk '{print $1+$2}')
  if [ "$n" = "0" ]; then
    sleep 30   # để metric hàng đợi cũng về 0 như lúc không có ai dùng
    python -X utf8 run.py sample spot 2>&1 | grep -E "job |Error|Traceback"
  else
    sleep 60
  fi
done
echo "loop done $(date +%T)"
