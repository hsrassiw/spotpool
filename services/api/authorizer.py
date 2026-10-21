import hashlib
import os
import boto3

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
USERS_TABLE = os.environ['USERS_TABLE'] # Không dùng giá trị mặc định theo hợp đồng

def lambda_handler(event, context):
    token = event.get('authorizationToken', '')
    if not token.startswith('Bearer '):
        return generate_policy('user', 'Deny', event['methodArn'])
    
    api_key = token.split(' ')[1]
    api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
    
    table = dynamodb.Table(USERS_TABLE)
    try:
        # Bắt buộc dùng GSI apiKeyHash-index thay cho scan theo hợp đồng
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
        else:
            return generate_policy('user', 'Deny', event['methodArn'])
    except Exception as e:
        print(f"Lỗi truy vấn DynamoDB: {str(e)}")
        return generate_policy('user', 'Deny', event['methodArn'])

def generate_policy(principal_id, effect, resource, context_data=None):
    auth_response = {'principalId': principal_id}
    if effect and resource:
        auth_response['policyDocument'] = {
            'Version': '2012-10-17',
            'Statement': [{'Action': 'execute-api:Invoke', 'Effect': effect, 'Resource': '*'}]
        }
    if context_data:
        auth_response['context'] = context_data
    return auth_response