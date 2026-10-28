import json
import os
import boto3

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
JOBS_TABLE = os.environ['JOBS_TABLE']

def lambda_handler(event, context):
    try:
        # Lấy userId đã được Lambda Authorizer xác thực và gắn vào requestContext
        user_id = event['requestContext']['authorizer']['userId']
        job_id = event.get('pathParameters', {}).get('id')
        
        if not job_id:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Missing jobId'})}
            
        jobs_table = dynamodb.Table(JOBS_TABLE)
        response = jobs_table.get_item(Key={'jobId': job_id})
        job = response.get('Item')
        
        # Kiểm tra job tồn tại và đúng chủ sở hữu
        if not job or job.get('userId') != user_id:
            return {'statusCode': 404, 'body': json.dumps({'error': 'Job not found'})}
            
        current_status = job.get('status')
        # Không cho phép hủy nếu job đã hoàn thành, đã lỗi hoặc đã hủy từ trước
        if current_status in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
            return {'statusCode': 400, 'body': json.dumps({'error': f'Cannot cancel job in {current_status} state'})}
            
        # Cập nhật trạng thái job thành CANCELLED trong DynamoDB
        jobs_table.update_item(
            Key={'jobId': job_id},
            UpdateExpression="SET #st = :c",
            ExpressionAttributeNames={'#st': 'status'},
            ExpressionAttributeValues={':c': 'CANCELLED'}
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Job cancelled successfully', 'jobId': job_id})
        }
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}