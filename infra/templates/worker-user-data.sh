#!/bin/bash
# User data cho worker EC2, Terraform điền các biến khi tạo launch template
set -euo pipefail

dnf install -y docker python3.13 python3.13-pip amazon-ecr-credential-helper
python3.13 -m pip install boto3
systemctl enable --now docker

# docker pull từ ECR dùng quyền của IAM role, không cần docker login
mkdir -p /root/.docker
echo '{"credHelpers": {"${registry}": "ecr-login"}}' > /root/.docker/config.json

mkdir -p /opt/spotpool/worker /opt/spotpool/spotpool_core
echo '${agent_py}' | base64 -d | gunzip > /opt/spotpool/worker/agent.py
echo '${contracts_py}' | base64 -d | gunzip > /opt/spotpool/spotpool_core/contracts.py
touch /opt/spotpool/spotpool_core/__init__.py

TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
md() { curl -s -H "X-aws-ec2-metadata-token: $TOKEN" "http://169.254.169.254/latest/meta-data/$1"; }

cat > /etc/spotpool-worker.env <<EOF
AWS_DEFAULT_REGION=${region}
JOBS_TABLE=Jobs
TASKS_TABLE=Tasks
USAGE_TABLE=Usage
SQS_ONDEMAND_URL=${sqs_ondemand_url}
SQS_SPOT_URL=${sqs_spot_url}
VIDEO_JOB_IMAGE=${video_image}
SCRIPT_JOB_IMAGE=${script_image}
TASK_MAX_SECONDS=7200
SPOTPOOL_QUEUE=${worker_queue}
SPOTPOOL_ASG=${worker_asg}
SPOTPOOL_INSTANCE_ID=$(md instance-id)
SPOTPOOL_INSTANCE_TYPE=$(md instance-type)
SPOTPOOL_AZ=$(md placement/availability-zone)
SPOTPOOL_LIFECYCLE=$(md instance-life-cycle)
EOF

cat > /etc/systemd/system/spotpool-worker.service <<EOF
[Unit]
Description=SpotPool worker
After=docker.service network-online.target
Requires=docker.service

[Service]
EnvironmentFile=/etc/spotpool-worker.env
Environment=PYTHONPATH=/opt/spotpool
Environment=HOME=/root
ExecStart=/usr/bin/python3.13 /opt/spotpool/worker/agent.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now spotpool-worker
