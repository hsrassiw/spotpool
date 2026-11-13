import json
import logging
import os
import hashlib
import secrets
import boto3

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
logger = logging.getLogger(__name__)
USERS_TABLE = os.environ['USERS_TABLE']

CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Authorization,Content-Type',
    'Access-Control-Allow-Methods': 'POST,OPTIONS',
}


def lambda_handler(event, context):
    try:
        # Lấy userId từ Lambda Authorizer truyền xuống sau khi xác thực token cũ thành công
        user_id = event['requestContext']['authorizer']['userId']
        
        # Sinh API Key mới
        new_api_key = f"sp_{secrets.token_hex(24)}"
        new_api_key_hash = hashlib.sha256(new_api_key.encode('utf-8')).hexdigest()
        
        table = dynamodb.Table(USERS_TABLE)
        
        # Cập nhật apiKeyHash mới vào bảng Users
        table.update_item(
            Key={'userId': user_id},
            UpdateExpression="SET apiKeyHash = :hash",
            ExpressionAttributeValues={':hash': new_api_key_hash}
        )
        
        return {
            'statusCode': 200,
            'headers': CORS_HEADERS,
            'body': json.dumps({
                'message': 'API key rotated successfully. Save your new API key.',
                'apiKey': new_api_key
            })
        }
    except Exception:
        logger.exception('Could not rotate API key')
        return {
            'statusCode': 500,
            'headers': CORS_HEADERS,
            'body': json.dumps({'error': 'Could not rotate API key'})
        }