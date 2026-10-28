import json
import os
import uuid
import hashlib
import secrets
import boto3
from decimal import Decimal

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
USERS_TABLE = os.environ['USERS_TABLE']

def decimal_default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError

def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
        user_id = body.get('userId') or body.get('email') or str(uuid.uuid4())
        
        # Sinh API Key ngẫu nhiên cho người dùng
        raw_api_key = f"sp_{secrets.token_hex(24)}"
        api_key_hash = hashlib.sha256(raw_api_key.encode('utf-8')).hexdigest()
        
        table = dynamodb.Table(USERS_TABLE)
        
        # Lưu vào bảng Users (tách creditAvailable và creditHeld theo thiết kế)
        user_item = {
            'userId': user_id,
            'apiKeyHash': api_key_hash,
            'creditAvailable': Decimal('50000'), # Credit khởi tạo ban đầu cho demo/test
            'creditHeld': Decimal('0'),
            'role': 'user',
            'limits': {
                'maxConcurrentJobs': 5,
                'freeErrorQuotaPerDay': 3
            }
        }
        
        table.put_item(Item=user_item)
        
        # Trả về API key cho người dùng (CHỈ HIỆN DUY NHẤT LẦN NÀY)
        return {
            'statusCode': 201,
            'body': json.dumps({
                'message': 'User registered successfully. Save your API key securely, it will not be shown again.',
                'userId': user_id,
                'apiKey': raw_api_key
            }, default=decimal_default)
        }
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}