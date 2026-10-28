import json
import uuid
import os
import time
import boto3
from helper import generate_presigned_upload_url

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
JOBS_TABLE = os.environ['JOBS_TABLE']
INPUT_BUCKET = os.environ['INPUT_BUCKET']
PRICE_ONDEMAND = int(os.environ['PRICE_ONDEMAND'])
PRICE_SPOT = int(os.environ['PRICE_SPOT'])

def lambda_handler(event, context):
    try:
        user_id = event['requestContext']['authorizer']['userId']
        body = json.loads(event.get('body', '{}'))
        
        tier = body.get('tier')
        kind = body.get('kind')
        vcpu = body.get('vcpu')
        
        # Kiểm tra validation tier, kind, vcpu theo yêu cầu
        if tier not in ['ondemand', 'spot'] or kind not in ['video', 'script'] or vcpu != 1:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Invalid tier, kind, or vcpu parameters'})}

        cost_cap = int(body.get('costCap', 15000))
        if cost_cap <= 0:
            return {'statusCode': 400, 'body': json.dumps({'error': 'costCap must be greater than 0'})}

        files = body.get('files', [])
        params = body.get('params', {})
        
        if not files or len(files) > 100:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Files list must contain between 1 and 100 items'})}
            
        job_id = str(uuid.uuid4())
        now = int(time.time())
        unit_price = PRICE_SPOT if tier == 'spot' else PRICE_ONDEMAND
        
        uploads = []
        formatted_files = []
        
        for idx, filename in enumerate(files, start=1):
            task_id = f"task-{idx:04d}"
            formatted_files.append(filename)
            s3_object_key = f"uploads/{user_id}/{job_id}/{task_id}"
            upload_url = generate_presigned_upload_url(INPUT_BUCKET, s3_object_key, expiration=3600)
            
            uploads.append({
                'taskId': task_id,
                'fileName': filename,
                'url': upload_url,
                'expiresIn': 3600
            })
            
        table = dynamodb.Table(JOBS_TABLE)
        job_item = {
            'jobId': job_id,
            'userId': user_id,
            'tier': tier,
            'kind': kind,
            'vcpu': vcpu,
            'image': 'spotpool-video' if kind == 'video' else 'spotpool-script',
            'params': params,
            'files': formatted_files,
            'status': 'UPLOADING',
            'costCap': cost_cap,
            'unitPrice': unit_price,
            'usedVcpuSeconds': 0,
            'taskCount': len(files),
            'tasksRemaining': len(files),
            'tasksSucceeded': 0,
            'tasksFailed': 0,
            'createdAt': now
        }
        table.put_item(Item=job_item)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'jobId': job_id,
                'status': 'UPLOADING',
                'uploads': uploads
            })
        }
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}