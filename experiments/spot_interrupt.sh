#!/bin/bash
# HM17: giả lập AWS thu hồi một máy Spot đang chạy task, thay cho AWS FIS.
# Cần apply trước: terraform apply -var enable_worker=true -var spot_interrupt_test=true
#
#   bash experiments/spot_interrupt.sh [instance-id]
#
# 1. Bật cảnh báo thu hồi trên máy (IMDS giả trả spot/instance-action).
# 2. Đợi 2 phút như AWS thật, trong lúc đó worker phải trả task về hàng đợi.
# 3. Tắt máy. Máy thay đã được ASG bật ngay khi worker tự tách khỏi ASG lúc nhận cảnh báo; task chạy lại trên đó.
set -euo pipefail
export MSYS_NO_PATHCONV=1

REGION=ap-southeast-1
ASG=spotpool-worker-spot
INSTANCE=${1:-}

if [ -z "$INSTANCE" ]; then
  INSTANCE=$(aws autoscaling describe-auto-scaling-groups --region "$REGION" --auto-scaling-group-names "$ASG" \
    --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`].InstanceId | [0]' --output text)
fi
if [ -z "$INSTANCE" ] || [ "$INSTANCE" = "None" ]; then
  echo "Không có máy Spot nào đang chạy. Nộp job gói Tiết kiệm trước." >&2
  exit 1
fi
echo "Máy: $INSTANCE"

run_on_instance() {
  local id
  id=$(aws ssm send-command --region "$REGION" --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
    --parameters "{\"commands\":[\"$1\"]}" --query Command.CommandId --output text)
  aws ssm wait command-executed --region "$REGION" --command-id "$id" --instance-id "$INSTANCE" || true
  aws ssm get-command-invocation --region "$REGION" --command-id "$id" --instance-id "$INSTANCE" \
    --query StandardOutputContent --output text
}

MOCK=$(run_on_instance "systemctl is-active spotpool-imds-mock" | tr -d '\r\n')
if [ "$MOCK" != "active" ]; then
  echo "IMDS giả không chạy ($MOCK). Apply lại với -var spot_interrupt_test=true rồi thử lại." >&2
  exit 1
fi

echo "$(date '+%H:%M:%S') Bật cảnh báo thu hồi"
run_on_instance "touch /run/spotpool-interrupt && date -u +%FT%TZ"

sleep 30
echo "Log worker 30 giây sau cảnh báo:"
run_on_instance "journalctl -u spotpool-worker -n 30 --no-pager"

sleep 90
echo "$(date '+%H:%M:%S') Hết 2 phút, tắt máy"
aws ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE" \
  --query 'TerminatingInstances[0].CurrentState.Name' --output text
echo "Xem task chạy lại trên máy mới: trạng thái và interruptCount trong bảng Tasks, số máy trên dashboard spotpool."
