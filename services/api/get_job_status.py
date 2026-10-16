import json
import os
import boto3
from helper import generate_presigned_download_url

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
JOBS_TABLE = os.environ.get('JOBS_TABLE', 'SpotPool_Jobs')
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET', 'spotpool-output-data')

def lambda_handler(event, context):
    """
    API get-job-status: Lay trang thai job va duong dan download ket qua
    """
    try:
        user_id = event['requestContext']['authorizer']['userId']
        job_id = event.get('pathParameters', {}).get('jobId')
        
        if not job_id:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Missing jobId'})}
            
        table = dynamodb.Table(JOBS_TABLE)
        response = table.get_item(Key={'jobId': job_id})
        job = response.get('Item')
        
        if not job or job.get('userId') != user_id:
            return {'statusCode': 440, 'body': json.dumps({'error': 'Job not found'})}
            
        download_url = None
        if job.get('status') == 'completed' and 'outputKey' in job:
            download_url = generate_presigned_download_url(OUTPUT_BUCKET, job['outputKey'])
            
        return {
            'statusCode': 200,
            'body': json.dumps({
                'jobId': job['jobId'],
                'status': job['status'],
                'createdAt': job['createdAt'],
                'downloadUrl': download_url
            })
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }