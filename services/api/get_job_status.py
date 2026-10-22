import json
import os
import boto3
from helper import generate_presigned_download_url

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
JOBS_TABLE = os.environ['JOBS_TABLE']
TASKS_TABLE = os.environ['TASKS_TABLE']
OUTPUT_BUCKET = os.environ['OUTPUT_BUCKET']

def lambda_handler(event, context):
    try:
        user_id = event['requestContext']['authorizer']['userId']
        job_id = event.get('pathParameters', {}).get('id')
        
        if not job_id:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Missing jobId'})}
            
        jobs_table = dynamodb.Table(JOBS_TABLE)
        job_resp = jobs_table.get_item(Key={'jobId': job_id})
        job = job_resp.get('Item')
        
        # Sửa mã lỗi 440 thành 404 chuẩn xác theo hợp đồng
        if not job or job.get('userId') != user_id:
            return {'statusCode': 404, 'body': json.dumps({'error': 'Job not found'})}
            
        tasks_table = dynamodb.Table(TASKS_TABLE)
        tasks_resp = tasks_table.query(
            KeyConditionExpression='jobId = :jid',
            ExpressionAttributeValues={':jid': job_id}
        )
        raw_tasks = tasks_resp.get('Items', [])
        
        tasks_output = []
        for t in raw_tasks:
            result_url = None
            if t.get('status') == 'SUCCEEDED' and 'resultKey' in t:
                result_url = generate_presigned_download_url(OUTPUT_BUCKET, t['resultKey'], expiration=900)
                
            log_url = None
            if 'logKey' in t:
                log_url = generate_presigned_download_url(OUTPUT_BUCKET, t['logKey'], expiration=900)
                
            tasks_output.append({
                'taskId': t.get('taskId'),
                'fileName': t.get('fileName'),
                'status': t.get('status'),
                'interruptCount': int(t.get('interruptCount', 0)),
                'resultUrl': result_url,
                'logUrl': log_url
            })
            
        return {
            'statusCode': 200,
            'body': json.dumps({
                'jobId': job['jobId'],
                'status': job['status'],
                'tier': job.get('tier'),
                'kind': job.get('kind'),
                'vcpu': int(job.get('vcpu', 1)),
                'costCap': int(job.get('costCap', 0)),
                'usedVcpuSeconds': int(job.get('usedVcpuSeconds', 0)),
                'progress': {
                    'total': int(job.get('taskCount', 0)),
                    'succeeded': int(job.get('tasksSucceeded', 0)),
                    'failed': int(job.get('tasksFailed', 0))
                },
                'createdAt': int(job.get('createdAt', 0)),
                'tasks': tasks_output
            })
        }
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}