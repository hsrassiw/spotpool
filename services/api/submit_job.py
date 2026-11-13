import json
import logging
import os
import time
import uuid
from decimal import Decimal
import math

import boto3
from helper import generate_presigned_upload_url

# Lambda luôn có AWS_REGION; giá trị mặc định chỉ dùng khi chạy test trên máy.
REGION = os.environ.get('AWS_REGION') or 'ap-southeast-1'

dynamodb = boto3.resource('dynamodb', region_name=REGION)
JOBS_TABLE = os.environ['JOBS_TABLE']
INPUT_BUCKET = os.environ['INPUT_BUCKET']
PRICE_ONDEMAND = int(os.environ['PRICE_ONDEMAND'])
PRICE_SPOT = int(os.environ['PRICE_SPOT'])

CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Authorization,Content-Type',
    'Access-Control-Allow-Methods': 'POST,OPTIONS',
}
logger = logging.getLogger(__name__)


def _response(status_code, payload):
    return {
        'statusCode': status_code,
        'headers': CORS_HEADERS,
        'body': json.dumps(payload),
    }


def _dynamodb_value(value):
    """Validate nested params before storing them or publishing a manifest."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('params numbers must be finite')
        return Decimal(str(value))
    if isinstance(value, list):
        return [_dynamodb_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _dynamodb_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError('params contains an unsupported value')


def _validated_request(body):
    if not isinstance(body, dict):
        raise ValueError('Request body must be a JSON object')

    tier = body.get('tier')
    kind = body.get('kind')
    vcpu = body.get('vcpu')
    if tier not in ('ondemand', 'spot') or kind not in ('video', 'script') or type(vcpu) is not int or vcpu != 1:
        raise ValueError('Invalid tier, kind, or vcpu parameters')

    cost_cap = body.get('costCap', 15000)
    if type(cost_cap) is not int or cost_cap <= 0:
        raise ValueError('costCap must be a positive integer')

    files = body.get('files')
    if (
        not isinstance(files, list) or not 1 <= len(files) <= 100
        or any(not isinstance(filename, str) or not filename.strip() for filename in files)
    ):
        raise ValueError('files must contain 1 to 100 non-empty file names')

    params = body.get('params', {})
    if not isinstance(params, dict):
        raise ValueError('params must be an object')
    params = _dynamodb_value(params)

    if kind == 'video':
        # Bộ lọc scale của FFmpeg cần chiều cao chẵn; kiểm tra sớm để từ chối cấu hình không hợp lệ.
        height = params.get('height', 720)
        if type(height) is not int or not 2 <= height <= 4320 or height % 2:
            raise ValueError('Video height must be an even integer between 2 and 4320')

    return tier, kind, vcpu, cost_cap, files, params


def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body') or '{}')
    except (TypeError, json.JSONDecodeError):
        return _response(400, {'error': 'Request body must be valid JSON'})

    try:
        tier, kind, vcpu, cost_cap, files, params = _validated_request(body)
    except ValueError as error:
        return _response(400, {'error': str(error)})

    try:
        user_id = event['requestContext']['authorizer']['userId']
        job_id = str(uuid.uuid4())
        now = int(time.time())
        unit_price = PRICE_SPOT if tier == 'spot' else PRICE_ONDEMAND

        uploads = []
        for idx, filename in enumerate(files, start=1):
            task_id = f"task-{idx:04d}"
            s3_object_key = f"uploads/{user_id}/{job_id}/{task_id}"
            upload_url = generate_presigned_upload_url(
                INPUT_BUCKET, s3_object_key, expiration=3600,
            )
            uploads.append({
                'taskId': task_id,
                'fileName': filename,
                'url': upload_url,
                'expiresIn': 3600,
            })

        job_item = {
            'jobId': job_id,
            'userId': user_id,
            'tier': tier,
            'kind': kind,
            'vcpu': vcpu,
            'image': 'spotpool-video' if kind == 'video' else 'spotpool-script',
            'params': params,
            'files': files,
            'status': 'UPLOADING',
            'costCap': cost_cap,
            'unitPrice': unit_price,
            'usedVcpuSeconds': 0,
            'taskCount': len(files),
            'tasksRemaining': len(files),
            'tasksSucceeded': 0,
            'tasksFailed': 0,
            'createdAt': now,
        }
        dynamodb.Table(JOBS_TABLE).put_item(Item=job_item)

        return _response(200, {
            'jobId': job_id,
            'status': 'UPLOADING',
            'uploads': uploads,
        })
    except Exception as error:
        logger.exception('Could not create job')
        return _response(500, {'error': 'Could not create job'})
