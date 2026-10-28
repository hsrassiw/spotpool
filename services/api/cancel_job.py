import json
import os
import boto3
from botocore.exceptions import ClientError

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
        
        try:
            jobs_table.update_item(
                Key={'jobId': job_id},
                UpdateExpression="SET #st = :c",
                ConditionExpression="#st IN (:u, :q, :r, :p) and userId = :uid",
                ExpressionAttributeNames={'#st': 'status'},
                ExpressionAttributeValues={
                    ':c': 'CANCELLED',
                    ':u': 'UPLOADING',
                    ':q': 'QUEUED',
                    ':r': 'RUNNING',
                    ':p': 'PAUSED',
                    ':uid': user_id
                }
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                return {
                    'statusCode': 404,
                    'headers': {'Access-Control-Allow-Origin': '*'},
                    'body': json.dumps({'error': 'Job not found or cannot be cancelled in its current state'})
                }
            raise e
        
        tasks_table = dynamodb.Table(TASKS_TABLE)
        tasks_resp = tasks_table.query(
            KeyConditionExpression='jobId = :jid',
            ExpressionAttributeValues={':jid': job_id}
        )
        tasks = tasks_resp.get('Items', [])
        
        for task in tasks:
            try:
                tasks_table.update_item(
                    Key={'jobId': job_id, 'taskId': task['taskId']},
                    UpdateExpression="SET #st = :c",
                    ConditionExpression="#st IN (:q, :r, :p)",
                    ExpressionAttributeNames={'#st': 'status'},
                    ExpressionAttributeValues={
                        ':c': 'CANCELLED',
                        ':q': 'QUEUED',
                        ':r': 'RUNNING',
                        ':p': 'PAUSED'
                    }
                )
            except ClientError as ce:
                if ce.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    continue
                raise ce
        
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