import json
import uuid
import os
import boto3
from datetime import datetime
from helper import generate_presigned_upload_url

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
JOBS_TABLE = os.environ.get('JOBS_TABLE', 'SpotPool_Jobs')
S3_BUCKET = os.environ.get('INPUT_BUCKET', 'spotpool-input-data')

def lambda_handler(event, context):
    """
    API submit-job: Nhan yeu cau nop job va tra ve presigned URL upload file
    """
    try:
        # Lay userId tu Lambda Authorizer truyen sang
        user_id = event['requestContext']['authorizer']['userId']
        body = json.loads(event.get('body', '{}'))
        
        package_type = body.get('packageType', 'saving') # 'stable' hoac 'saving'
        cost_cap = body.get('costCap', 10.0)
        filename = body.get('filename', 'input_data.bin')
        
        job_id = str(uuid.uuid4())
        s3_object_key = f"uploads/{user_id}/{job_id}/{filename}"
        
        # Sinh presigned URL cho phep nguoi dung upload file len S3
        upload_url = generate_presigned_upload_url(S3_BUCKET, s3_object_key)
        
        # Luu thong tin job vao DynamoDB
        table = dynamodb.Table(JOBS_TABLE)
        job_item = {
            'jobId': job_id,
            'userId': user_id,
            'status': 'pending',
            'packageType': package_type,
            'costCap': str(cost_cap),
            'inputUri': f"s3://{S3_BUCKET}/{s3_object_key}",
            'createdAt': datetime.utcnow().isoformat()
        }
        table.put_item(Item=job_item)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Job submitted successfully',
                'jobId': job_id,
                'uploadUrl': upload_url
            })
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }