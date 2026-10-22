import json
import os
import boto3

dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

JOBS_TABLE = os.environ['JOBS_TABLE']
USERS_TABLE = os.environ['USERS_TABLE']
INPUT_BUCKET = os.environ['INPUT_BUCKET']

def lambda_handler(event, context):
    try:
        path_parameters = event.get('pathParameters', {}) or {}
        job_id = path_parameters.get('id')
        body = json.loads(event.get('body', '{}'))
        
        # Ép kiểu int cho vcpu
        vcpu = int(body.get('vcpu', 1))
        user_id = body.get('userId')
        credit_cost = int(body.get('credit', 10))

        client = boto3.client('dynamodb')

        # Sử dụng TransactWriteItems để trừ credit ở bảng Users và cập nhật trạng thái job
        try:
            client.transact_write_items(
                TransactItems=[
                    {
                        'Update': {
                            'TableName': USERS_TABLE,
                            'Key': {'userId': {'S': user_id}},
                            'UpdateExpression': 'SET credit = credit - :c',
                            'ConditionExpression': 'credit >= :c',
                            'ExpressionAttributeValues': {':c': {'N': str(credit_cost)}}
                        }
                    },
                    {
                        'Update': {
                            'TableName': JOBS_TABLE,
                            'Key': {'jobId': {'S': job_id}},
                            'UpdateExpression': 'SET #st = :q, vcpu = :v',
                            'ExpressionAttributeNames': {'#st': 'status'},
                            'ExpressionAttributeValues': {
                                ':q': {'S': 'QUEUED'},
                                ':v': {'N': str(vcpu)}
                            }
                        }
                    }
                ]
            )
        except client.exceptions.TransactionCanceledException:
            return {
                'statusCode': 400,
                'body': json.dumps({'message': 'Insufficient credit or invalid user'})
            }

        # Sau khi đã sang hàng đợi (QUEUED) thành công, tiến hành ghi file manifest.json lên S3
        manifest_data = {
            'jobId': job_id,
            'vcpu': vcpu,
            'status': 'QUEUED'
        }
        
        s3.put_object(
            Bucket=INPUT_BUCKET,
            Key=f"manifests/{job_id}/manifest.json",
            Body=json.dumps(manifest_data),
            ContentType='application/json'
        )

        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Job started successfully and manifest written'})
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }