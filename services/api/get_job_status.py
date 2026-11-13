import json
import logging
import os
import boto3
from decimal import Decimal
from helper import generate_presigned_download_url

# Lambda luôn có AWS_REGION; giá trị mặc định chỉ dùng khi chạy test trên máy.
REGION = os.environ.get('AWS_REGION') or 'ap-southeast-1'

dynamodb = boto3.resource('dynamodb', region_name=REGION)
logger = logging.getLogger(__name__)
JOBS_TABLE = os.environ['JOBS_TABLE']
TASKS_TABLE = os.environ['TASKS_TABLE']
OUTPUT_BUCKET = os.environ['OUTPUT_BUCKET']

def decimal_default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError

def result_file(kind, file_name):
    """Tên và kiểu file kết quả: job Python ra văn bản UTF-8, job video ra MP4."""
    stem = (file_name or 'task').rsplit('/', 1)[-1]
    stem = stem.rsplit('.', 1)[0] if '.' in stem.strip('.') else stem
    if kind == 'video':
        return {'filename': f'{stem}-ketqua.mp4', 'content_type': 'video/mp4'}
    return {'filename': f'{stem}-ketqua.txt', 'content_type': 'text/plain; charset=utf-8'}


def log_file(file_name):
    """Log luôn là văn bản UTF-8. Không khai báo charset thì S3 trả binary/octet-stream:
    trình duyệt tải file về và mở bằng bảng mã mặc định của máy, làm hỏng tiếng Việt."""
    stem = (file_name or 'task').rsplit('/', 1)[-1]
    stem = stem.rsplit('.', 1)[0] if '.' in stem.strip('.') else stem
    return {'filename': f'{stem}-log.txt', 'content_type': 'text/plain; charset=utf-8'}


def lambda_handler(event, context):
    try:
        user_id = event['requestContext']['authorizer']['userId']
        job_id = event.get('pathParameters', {}).get('id')
        
        if not job_id:
            return {
                'statusCode': 400,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Missing jobId'})
            }
            
        jobs_table = dynamodb.Table(JOBS_TABLE)
        job_resp = jobs_table.get_item(Key={'jobId': job_id})
        job = job_resp.get('Item')
        
        if not job or job.get('userId') != user_id:
            return {
                'statusCode': 404,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Job not found'})
            }
            
        tasks_table = dynamodb.Table(TASKS_TABLE)
        tasks_resp = tasks_table.query(
            KeyConditionExpression='jobId = :jid',
            ExpressionAttributeValues={':jid': job_id}
        )
        raw_tasks = tasks_resp.get('Items', [])
        
        has_interrupted_task = False
        tasks_output = []
        for t in raw_tasks:
            interrupt_count = int(t.get('interruptCount', 0))
            if t.get('status') in ('QUEUED', 'RUNNING') and interrupt_count > 0:
                has_interrupted_task = True

            result_url = None
            if t.get('status') == 'SUCCEEDED' and 'resultKey' in t:
                # Worker lưu kết quả với key không đuôi; đặt tên và kiểu file khi cấp link để trình duyệt mở đúng.
                result_url = generate_presigned_download_url(OUTPUT_BUCKET, t['resultKey'], expiration=900,
                                                             **result_file(job.get('kind'), t.get('fileName')))
                
            log_url = None
            if 'logKey' in t:
                log_url = generate_presigned_download_url(OUTPUT_BUCKET, t['logKey'], expiration=900,
                                                          **log_file(t.get('fileName')))
                
            tasks_output.append({
                'taskId': t.get('taskId'),
                'fileName': t.get('fileName'),
                'status': t.get('status'),
                'interruptCount': interrupt_count,
                # Lần gần nhất worker lưu checkpoint của task (job script hoặc job video); không có thì null.
                'checkpointAt': int(t['checkpointAt']) if 'checkpointAt' in t else None,
                'resultUrl': result_url,
                'logUrl': log_url
            })
            
        charged_credit = int(job['chargedCredit']) if 'chargedCredit' in job else None
            
        response_data = {
            'jobId': job['jobId'],
            'status': job['status'],
            'interrupted': has_interrupted_task,
            'tier': job.get('tier'),
            'kind': job.get('kind'),
            'vcpu': int(job.get('vcpu', 1)),
            'costCap': int(job.get('costCap', 0)),
            'chargedCredit': charged_credit,
            'usedVcpuSeconds': int(job.get('usedVcpuSeconds', 0)),
            'progress': {
                'total': int(job.get('taskCount', 0)),
                'succeeded': int(job.get('tasksSucceeded', 0)),
                'failed': int(job.get('tasksFailed', 0))
            },
            'createdAt': int(job.get('createdAt', 0)),
            'queuedAt': int(job.get('queuedAt', 0)) if 'queuedAt' in job else None,
            'startedAt': int(job.get('startedAt', 0)) if 'startedAt' in job else None,
            'finishedAt': int(job.get('finishedAt', 0)) if 'finishedAt' in job else None,
            'tasks': tasks_output
        }
            
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Authorization,Content-Type'
            },
            'body': json.dumps(response_data, default=decimal_default)
        }
    except Exception:
        logger.exception('Could not load job')
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Authorization,Content-Type'
            },
            'body': json.dumps({'error': 'Could not load job'})
        }