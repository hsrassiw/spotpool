import json
import logging
import os
import time
import boto3
from decimal import Decimal
from botocore.exceptions import ClientError

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
dynamodb_client = boto3.client('dynamodb', region_name='ap-southeast-1')
s3_client = boto3.client('s3', region_name='ap-southeast-1')

logger = logging.getLogger(__name__)
JOBS_TABLE = os.environ['JOBS_TABLE']
USERS_TABLE = os.environ['USERS_TABLE']
INPUT_BUCKET = os.environ['INPUT_BUCKET']
OUTPUT_BUCKET = os.environ['OUTPUT_BUCKET']
# Mỗi máy worker chạy 2 suất trên ổ đĩa chung; worker/agent.py dùng cùng giới hạn.
MAX_INPUT_BYTES = 500 * 1024 * 1024

def convert_decimals(obj):
    """Đổi tất cả kiểu Decimal thành int/float trước khi json.dumps"""
    if isinstance(obj, list):
        return [convert_decimals(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj

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
            
        # Thu thập thông tin file trên S3
        missing_files = []
        too_large = []
        task_count = int(job.get('taskCount', 0))
        files = job.get('files', [])
        inputs = []
        for idx in range(1, task_count + 1):
            task_id = f"task-{idx:04d}"
            s3_key = f"uploads/{user_id}/{job_id}/{task_id}"
            file_name = files[idx - 1] if idx - 1 < len(files) else task_id
            try:
                head = s3_client.head_object(Bucket=INPUT_BUCKET, Key=s3_key)
            except ClientError:
                missing_files.append(task_id)
                continue
            # Link PUT không giới hạn được dung lượng, nên chặn ở đây trước khi giữ credit.
            if int(head.get('ContentLength', 0)) > MAX_INPUT_BYTES:
                too_large.append(file_name)
                continue
            inputs.append({
                'taskId': task_id,
                'fileName': file_name,
                'bucket': INPUT_BUCKET,
                'key': s3_key
            })

        if missing_files:
            return {
                'statusCode': 400,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Missing files on S3', 'missingFiles': missing_files})
            }
        if too_large:
            return {
                'statusCode': 400,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': f'Each file must be at most {MAX_INPUT_BYTES // (1024 * 1024)} MB',
                                    'maxBytes': MAX_INPUT_BYTES, 'tooLargeFiles': too_large})
            }

        # Đảm bảo params sạch Decimal trước khi ghi manifest
        clean_params = convert_decimals(job.get('params', {}))
        manifest_data = {
            'schemaVersion': 1,
            'jobId': job_id,
            'userId': user_id,
            'tier': job.get('tier'),
            'kind': job.get('kind'),
            'vcpu': int(job.get('vcpu', 1)),
            'image': job.get('image'),
            'params': clean_params,
            'outputBucket': OUTPUT_BUCKET,
            'inputs': inputs
        }
        manifest_key = f"submissions/{user_id}/{job_id}/manifest.json"

        # Nếu job đã QUEUED từ trước, ghi lại manifest rồi trả về 200
        if job.get('status') == 'QUEUED':
            s3_client.put_object(
                Bucket=INPUT_BUCKET,
                Key=manifest_key,
                Body=json.dumps(manifest_data),
                ContentType='application/json'
            )
            return {
                'statusCode': 200,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'message': 'Job already queued, manifest updated', 'jobId': job_id})
            }
            
        if job.get('status') != 'UPLOADING':
            return {
                'statusCode': 400,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Job is not in UPLOADING state'})
            }

        cost_cap = int(job.get('costCap', 0))
        now = int(time.time())

        # Gộp giữ credit, tăng runningJobs và đổi trạng thái QUEUED trong 1 TransactWriteItems nguyên tử
        try:
            dynamodb_client.transact_write_items(
                TransactItems=[
                    {
                        'Update': {
                            'TableName': USERS_TABLE,
                            'Key': {'userId': {'S': user_id}},
                            'UpdateExpression': 'SET creditAvailable = creditAvailable - :cap, creditHeld = if_not_exists(creditHeld, :zero) + :cap, runningJobs = if_not_exists(runningJobs, :zero) + :one',
                            'ConditionExpression': 'creditAvailable >= :cap AND (attribute_not_exists(runningJobs) OR runningJobs < maxConcurrentJobs)',
                            'ExpressionAttributeValues': {
                                ':cap': {'N': str(cost_cap)},
                                ':zero': {'N': '0'},
                                ':one': {'N': '1'}
                            }
                        }
                    },
                    {
                        'Update': {
                            'TableName': JOBS_TABLE,
                            'Key': {'jobId': {'S': job_id}},
                            'UpdateExpression': 'SET #st = :q, queuedAt = :t',
                            'ConditionExpression': '#st = :up',
                            'ExpressionAttributeNames': {'#st': 'status'},
                            'ExpressionAttributeValues': {
                                ':q': {'S': 'QUEUED'},
                                ':t': {'N': str(now)},
                                ':up': {'S': 'UPLOADING'}
                            }
                        }
                    }
                ]
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'TransactionCanceledException':
                return {
                    'statusCode': 400,
                    'headers': {'Access-Control-Allow-Origin': '*'},
                    'body': json.dumps({'error': 'Insufficient credit or maxConcurrentJobs limit reached'})
                }
            raise e

        # Ghi manifest sau khi transaction thành công
        s3_client.put_object(
            Bucket=INPUT_BUCKET,
            Key=manifest_key,
            Body=json.dumps(manifest_data),
            ContentType='application/json'
        )
        
        return {
            'statusCode': 200,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'message': 'Job started successfully', 'jobId': job_id})
        }
    except Exception:
        logger.exception('Could not start job')
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Could not start job'})
        }