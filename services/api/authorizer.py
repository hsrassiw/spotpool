import hashlib
import os
import boto3

# Khởi tạo DynamoDB client trong region Singapore (ap-southeast-1)
dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
USERS_TABLE = os.environ.get('USERS_TABLE', 'SpotPool_Users')

def lambda_handler(event, context):
    """
    Lambda Authorizer: Kiểm tra API Key từ Request Header
    Header mẫu: Authorization: Bearer <API_KEY>
    """
    token = event.get('authorizationToken', '')
    
    # Kiểm tra định dạng Authorization Bearer
    if not token.startswith('Bearer '):
        return generate_policy('user', 'Deny', event['methodArn'])
    
    api_key = token.split(' ')[1]
    
    # Băm API Key bằng SHA-256 để so sánh với cơ sở dữ liệu
    api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
    
    # Tra cứu trong bảng DynamoDB Users
    table = dynamodb.Table(USERS_TABLE)
    try:
        response = table.scan(
            FilterExpression='apiKeyHash = :hash',
            ExpressionAttributeValues={':hash': api_key_hash}
        )
        items = response.get('Items', [])
        
        if items:
            user = items[0]
            user_id = user['userId']
            # Cho phép truy cập và đính kèm thông tin userId vào context
            return generate_policy(user_id, 'Allow', event['methodArn'], context_data={'userId': user_id})
        else:
            # Không tìm thấy API Key hợp lệ
            return generate_policy('user', 'Deny', event['methodArn'])
            
    except Exception as e:
        print(f"Loi truy van DynamoDB: {str(e)}")
        return generate_policy('user', 'Deny', event['methodArn'])

def generate_policy(principal_id, effect, resource, context_data=None):
    """
    Hàm tạo IAM Policy phản hồi lại cho API Gateway
    """
    auth_response = {
        'principalId': principal_id
    }
    
    if effect and resource:
        policy_document = {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': effect,
                'Resource': resource
            }]
        }
        auth_response['policyDocument'] = policy_document
        
    if context_data:
        auth_response['context'] = context_data
        
    return auth_response