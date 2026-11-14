#!/bin/bash
# Đo thời gian worker sẵn sàng: bật mới (cold) và bật lại từ stopped (warm pool), rồi xóa máy thử.
set -uo pipefail
export AWS_DEFAULT_REGION=ap-southeast-1
SUBNET=subnet-0b0d5e5905e74b809
CYCLES=3
log() { echo "$(date +%H:%M:%S) $*"; }

IID=$(aws ec2 run-instances --launch-template LaunchTemplateName=spotpool-worker-ondemand,Version='$Latest' \
  --subnet-id "$SUBNET" --count 1 \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=spotpool-warmpool-test},{Key=Project,Value=spotpool}]' \
  --query 'Instances[0].InstanceId' --output text)
T0=$(date +%s)
log "launched $IID"
trap 'log "terminating $IID"; aws ec2 terminate-instances --instance-ids "$IID" >/dev/null' EXIT

# Chạy lệnh qua SSM, in stdout
ssm() {
  local cid out st
  for _ in $(seq 1 60); do
    cid=$(aws ssm send-command --instance-ids "$IID" --document-name AWS-RunShellScript \
      --parameters "commands=[\"$1\"]" --query Command.CommandId --output text 2>/dev/null) && break
    sleep 5
  done
  for _ in $(seq 1 60); do
    sleep 3
    st=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$IID" --query Status --output text 2>/dev/null)
    case "$st" in Success|Failed) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$cid" --instance-id "$IID" --query StandardOutputContent --output text
}

# Đợi worker active rồi lấy mốc giờ (epoch) worker vào trạng thái active trong lần boot này
worker_epoch() {
  for _ in $(seq 1 60); do
    out=$(ssm "systemctl is-active spotpool-worker && date -d \\\"\$(systemctl show -p ActiveEnterTimestamp --value spotpool-worker)\\\" +%s")
    ep=$(echo "$out" | tail -1 | tr -d '[:space:]')
    if echo "$out" | head -1 | grep -q '^active' && [[ "$ep" =~ ^[0-9]+$ ]]; then echo "$ep"; return; fi
    sleep 5
  done
  echo 0
}

E=$(worker_epoch)
log "COLD: worker active sau $((E - T0)) s kể từ lệnh bật máy"
ssm "journalctl -u spotpool-worker -b --no-pager -n 3" | sed 's/^/   /'

for c in $(seq 1 $CYCLES); do
  aws ec2 stop-instances --instance-ids "$IID" >/dev/null
  aws ec2 wait instance-stopped --instance-ids "$IID"
  log "stopped (cycle $c)"
  aws ec2 start-instances --instance-ids "$IID" >/dev/null
  T1=$(date +%s)
  E=$(worker_epoch)
  log "WARM $c: worker active sau $((E - T1)) s kể từ lệnh start"
  ssm "journalctl -u spotpool-worker -b --no-pager -n 2; docker images --format '{{.Repository}}' | wc -l" | sed 's/^/   /'
done
