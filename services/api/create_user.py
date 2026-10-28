import json
import os
import uuid
import hashlib
import secrets
import time
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
        email = body.get('email') or ""
        
        # Luôn tạo userId bằng uuid4
        user_id = str(uuid.uuid4())
        now = int(time.time())
        
        # Sinh API Key ngẫu nhiên
        raw_api_key = f"sp_{secrets.token_hex(24)}"
        api_key_hash = hashlib.sha256(raw_api_key.encode('utf-8')).hexdigest()
        
        table = dynamodb.Table(USERS_TABLE)
        
        user_item = {
            'userId': user_id,
            'email': email,
            'apiKeyHash': api_key_hash,
            'creditAvailable': Decimal('50000'),
            'creditHeld': Decimal('0'),
            'role': 'user',
            'runningJobs': Decimal('0'),
            'maxConcurrentJobs': Decimal('2'),
            'freeErrorQuotaPerDay': Decimal('3'),
            'createdAt': Decimal(str(now))
        }
        
        # Tránh ghi đè tài khoản khác
        table.put_item(
            Item=user_item,
            ConditionExpression="attribute_not_exists(userId)"
        )
        
        return {
            'statusCode': 201,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': '*'
            },
            'body': json.dumps({
                'message': 'User registered successfully. Save your API key securely, it will not be shown again.',
                'userId': user_id,
                'email': email,
                'apiKey': raw_api_key
            }, default=decimal_default)
        }
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': '*'
            },
            'body': json.dumps({'error': str(e)})
        }