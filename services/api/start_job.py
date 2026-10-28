import json
import os
import time
import boto3
from decimal import Decimal
from botocore.exceptions import ClientError

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
s3_client = boto3.client('s3', region_name='ap-southeast-1')

JOBS_TABLE = os.environ['JOBS_TABLE']
USERS_TABLE = os.environ['USERS_TABLE']
INPUT_BUCKET = os.environ['INPUT_BUCKET']
OUTPUT_BUCKET = os.environ['OUTPUT_BUCKET']

def lambda_handler(event, context):
    try:
        user_id = event['requestContext']['authorizer']['userId']
        job_id = event.get('pathParameters', {}).get('id')
        
        if not job_id:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Missing jobId'})}
            
        jobs_table = dynamodb.Table(JOBS_TABLE)
        users_table = dynamodb.Table(USERS_TABLE)
        
        job_resp = jobs_table.get_item(Key={'jobId': job_id})
        job = job_resp.get('Item')
        
        if not job or job.get('userId') != user_id:
            return {'statusCode': 404, 'body': json.dumps({'error': 'Job not found'})}
            
        if job.get('status') == 'QUEUED':
            return {'statusCode': 200, 'body': json.dumps({'message': 'Job already queued'})}
            
        if job.get('status') != 'UPLOADING':
            return {'statusCode': 400, 'body': json.dumps({'error': 'Job is not in UPLOADING state'})}
            
        cost_cap = int(job.get('costCap', 0))
        
        # Kiểm tra file trên S3 như cũ...
        missing_files = []
        task_count = int(job.get('taskCount', 0))
        files = job.get('files', [])
        inputs = []
        for idx in range(1, task_count + 1):
            task_id = f"task-{idx:04d}"
            s3_key = f"uploads/{user_id}/{job_id}/{task_id}"
            try:
                s3_client.head_object(Bucket=INPUT_BUCKET, Key=s3_key)
                inputs.append({
                    'taskId': task_id,
                    'fileName': files[idx - 1] if idx - 1 < len(files) else task_id,
                    'bucket': INPUT_BUCKET,
                    'key': s3_key
                })
            except ClientError:
                missing_files.append(task_id)
                
        if missing_files:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Missing files on S3', 'missingFiles': missing_files})}
            
        # GIỮ CREDIT NGUYÊN TỬ: creditAvailable >= costCap
        try:
            users_table.update_item(
                Key={'userId': user_id},
                UpdateExpression="SET creditAvailable = creditAvailable - :cap, creditHeld = if_not_exists(creditHeld, :zero) + :cap",
                ConditionExpression="creditAvailable >= :cap",
                ExpressionAttributeValues={
                    ':cap': Decimal(str(cost_cap)),
                    ':zero': Decimal('0')
                }
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                return {'statusCode': 400, 'body': json.dumps({'error': 'Insufficient creditAvailable for costCap'})}
            raise e

        # Chuyển trạng thái sang QUEUED
        now = int(time.time())
        jobs_table.update_item(
            Key={'jobId': job_id},
            UpdateExpression="SET #st = :q, queuedAt = :t",
            ExpressionAttributeNames={'#st': 'status'},
            ExpressionAttributeValues={':q': 'QUEUED', ':t': now, ':up': 'UPLOADING'},
            ConditionExpression="#st = :up"
        )

        manifest_data = {
            'schemaVersion': 1,
            'jobId': job_id,
            'userId': user_id,
            'tier': job.get('tier'),
            'kind': job.get('kind'),
            'vcpu': int(job.get('vcpu', 1)),
            'image': job.get('image'),
            'params': job.get('params', {}),
            'outputBucket': OUTPUT_BUCKET,
            'inputs': inputs
        }
        s3_client.put_object(
            Bucket=INPUT_BUCKET,
            Key=f"submissions/{user_id}/{job_id}/manifest.json",
            Body=json.dumps(manifest_data),
            ContentType='application/json'
        )
        
        return {'statusCode': 200, 'body': json.dumps({'message': 'Job started successfully', 'jobId': job_id})}
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}