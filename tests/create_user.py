import boto3
import hashlib

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
table = dynamodb.Table('Users')

test_key = "test-api-key-12345"
api_key_hash = hashlib.sha256(test_key.encode('utf-8')).hexdigest()

table.put_item(
    Item={
        'userId': 'test-user-001',
        'apiKeyHash': api_key_hash,
        'credit': 100000,
        'role': 'user'
    }
)
print(f"Đã tạo user test thành công! API Key của bạn là: {test_key}")
print(f"Mã băm SHA-256 lưu trong DB: {api_key_hash}")