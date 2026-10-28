import json
import os
import boto3

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
JOBS_TABLE = os.environ['JOBS_TABLE']
TASKS_TABLE = os.environ['TASKS_TABLE']

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
        response = jobs_table.get_item(Key={'jobId': job_id})
        job = response.get('Item')
        
        if not job or job.get('userId') != user_id:
            return {
                'statusCode': 404,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Job not found'})
            }
            
        current_status = job.get('status')
        valid_cancel_statuses = ['UPLOADING', 'QUEUED', 'RUNNING', 'PAUSED']
        
        if current_status not in valid_cancel_statuses:
            return {
                'statusCode': 400,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': f'Cannot cancel job in {current_status} state'})
            }
            
        # Cập nhật trạng thái job thành CANCELLED
        jobs_table.update_item(
            Key={'jobId': job_id},
            UpdateExpression="SET #st = :c",
            ExpressionAttributeNames={'#st': 'status'},
            ExpressionAttributeValues={':c': 'CANCELLED'}
        )
        
        # Chuyển các task đang chờ/chạy sang CANCELLED
        tasks_table = dynamodb.Table(TASKS_TABLE)
        tasks_resp = tasks_table.query(
            KeyConditionExpression='jobId = :jid',
            ExpressionAttributeValues={':jid': job_id}
        )
        tasks = tasks_resp.get('Items', [])
        
        for task in tasks:
            if task.get('status') in ['QUEUED', 'RUNNING', 'PAUSED']:
                tasks_table.update_item(
                    Key={'jobId': job_id, 'taskId': task['taskId']},
                    UpdateExpression="SET #st = :c",
                    ExpressionAttributeNames={'#st': 'status'},
                    ExpressionAttributeValues={':c': 'CANCELLED'}
                )
        
        return {
            'statusCode': 200,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'message': 'Job cancelled successfully', 'jobId': job_id})
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': str(e)})
        }