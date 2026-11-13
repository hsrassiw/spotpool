import hashlib
import os
import boto3

# Lambda luôn có AWS_REGION; giá trị mặc định chỉ dùng khi chạy test trên máy.
REGION = os.environ.get('AWS_REGION') or 'ap-southeast-1'

dynamodb = boto3.resource('dynamodb', region_name=REGION)
USERS_TABLE = os.environ['USERS_TABLE'] # Không dùng giá trị mặc định theo hợp đồng

def lambda_handler(event, context):
    token = event.get('authorizationToken') or ''
    if not isinstance(token, str):
        raise Exception('Unauthorized')
    scheme, separator, api_key = token.partition(' ')
    if scheme != 'Bearer' or not separator or not api_key:
        raise Exception('Unauthorized')

    api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()

    table = dynamodb.Table(USERS_TABLE)
    # Lỗi DynamoDB phải nổi lên thành lỗi xác thực, không bị cache nhầm thành Deny trong 5 phút.
    response = table.query(
        IndexName='apiKeyHash-index',
        KeyConditionExpression='apiKeyHash = :hash',
        ExpressionAttributeValues={':hash': api_key_hash}
    )
    items = response.get('Items', [])

    if items:
        user = items[0]
        user_id = user['userId']
        role = user.get('role', 'user')
        return generate_policy(user_id, 'Allow', event['methodArn'], context_data={'userId': user_id, 'role': role})
    raise Exception('Unauthorized')

def generate_policy(principal_id, effect, resource, context_data=None):
    auth_response = {'principalId': principal_id}
    if effect and resource:
        auth_response['policyDocument'] = {
            'Version': '2012-10-17',
            'Statement': [{'Action': 'execute-api:Invoke', 'Effect': effect,
                           'Resource': '/'.join(resource.split('/')[:2]) + '/*/*'}]
        }
    if context_data:
        auth_response['context'] = context_data
    return auth_response
