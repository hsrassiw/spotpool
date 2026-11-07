#!/bin/bash
# User data cho worker EC2, Terraform điền các biến khi tạo launch template
set -euo pipefail

dnf install -y docker python3.13 python3.13-pip amazon-ecr-credential-helper
python3.13 -m pip install boto3
systemctl enable --now docker

# docker pull từ ECR dùng quyền của IAM role, không cần docker login
mkdir -p /root/.docker
echo '{"credHelpers": {"${registry}": "ecr-login"}}' > /root/.docker/config.json

# Code worker tải từ S3 (qua S3 Gateway Endpoint), mỗi lần sửa code là một thư mục mới
%{ for f in worker_files ~}
aws s3 cp --region ${region} --only-show-errors "${code_url}/${f}" "/opt/spotpool/${f}"
%{ endfor ~}

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

%{ if imds_mock ~}
# Test HM17: worker đọc metadata qua IMDS giả, touch /run/spotpool-interrupt để giả lập cảnh báo thu hồi
echo "SPOTPOOL_IMDS_BASE=http://127.0.0.1:1338/latest" >> /etc/spotpool-worker.env
cat > /etc/systemd/system/spotpool-imds-mock.service <<EOF
[Unit]
Description=SpotPool IMDS mock (HM17)

[Service]
ExecStart=/usr/bin/python3.13 /opt/spotpool/worker/imds_mock.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now spotpool-imds-mock
%{ endif ~}

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
