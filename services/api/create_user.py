import json
import logging
import os
import uuid
import hashlib
import secrets
import time
import boto3
from decimal import Decimal

# Lambda luôn có AWS_REGION; giá trị mặc định chỉ dùng khi chạy test trên máy.
REGION = os.environ.get('AWS_REGION') or 'ap-southeast-1'

dynamodb = boto3.resource('dynamodb', region_name=REGION)
logger = logging.getLogger(__name__)
USERS_TABLE = os.environ['USERS_TABLE']

def decimal_default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError

def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body') or '{}')
        if not isinstance(body, dict):
            raise ValueError('Request body must be a JSON object')
        email = body.get('email')
        if not isinstance(email, str) or not email.strip():
            raise ValueError('email must be a non-empty string')
    except (TypeError, ValueError) as error:
        return {
            'statusCode': 400,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Authorization,Content-Type'
            },
            'body': json.dumps({'error': str(error)})
        }
    try:
        
        user_id = str(uuid.uuid4())
        now = int(time.time())
        
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
            # Mức mặc định theo hợp đồng; API chạy job sẽ kiểm tra giới hạn này một cách nguyên tử.
            'maxConcurrentJobs': Decimal('2'),
            'freeErrorQuotaPerDay': Decimal('3'),
            'createdAt': Decimal(str(now))
        }
        
        table.put_item(
            Item=user_item,
            ConditionExpression="attribute_not_exists(userId)"
        )
        
        return {
            'statusCode': 201,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Authorization,Content-Type'
            },
            'body': json.dumps({
                'message': 'User registered successfully. Save your API key securely, it will not be shown again.',
                'userId': user_id,
                'email': email,
                'apiKey': raw_api_key
            }, default=decimal_default)
        }
    except Exception:
        logger.exception('Could not create user')
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Authorization,Content-Type'
            },
            'body': json.dumps({'error': 'Could not create user'})
        }
