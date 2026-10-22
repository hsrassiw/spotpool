import json
import os
import boto3
import time
from botocore.exceptions import ClientError

dynamodb = boto3.resource('dynamodb')
JOBS_TABLE = os.environ['JOBS_TABLE']
TASKS_TABLE = os.environ['TASKS_TABLE']

def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        
        tier = body.get('tier')
        kind = body.get('kind')
        vcpu = body.get('vcpu')
        
        # Validate đúng yêu cầu: tier (ondemand/spot), kind (video/script), vcpu bắt buộc bằng 1
        if tier not in ['ondemand', 'spot'] or kind not in ['video', 'script'] or vcpu != 1:
            return {
                'statusCode': 400,
                'body': json.dumps({'message': 'Invalid tier, kind, or vcpu parameters'})
            }
            
        job_id = body.get('jobId')
        files = body.get('files', [])
        
        jobs_table = dynamodb.Table(JOBS_TABLE)
        tasks_table = dynamodb.Table(TASKS_TABLE)
        
        # Lưu thông tin job với trạng thái ban đầu UPLOADING, epoch (int), credit (int)
        jobs_table.put_item(Item={
            'jobId': job_id,
            'tier': tier,
            'kind': kind,
            'vcpu': int(vcpu),
            'status': 'UPLOADING',
            'createdAt': int(time.time())
        })
        
        # Xử lý danh sách file theo taskId
        for file_item in files:
            task_id = file_item.get('taskId')
            tasks_table.put_item(Item={
                'taskId': task_id,
                'jobId': job_id,
                'fileKey': file_item.get('key'),
                'status': 'UPLOADING'
            })
            
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Job submitted successfully', 'jobId': job_id})
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }