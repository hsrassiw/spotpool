import boto3
import hashlib
import uuid

dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
table = dynamodb.Table('Users')

test_key = "test-api-key-12345"
api_key_hash = hashlib.sha256(test_key.encode('utf-8')).hexdigest()
user_id = str(uuid.uuid4())

table.put_item(
    Item={
        'userId': user_id,
        'apiKeyHash': api_key_hash,
        'creditAvailable': 100000,
        'creditHeld': 0,
        'role': 'user'
    }
)

print(f"Đã tạo user test thành công! userId (UUIDv4): {user_id}")
print(f"API Key của bạn là: {test_key}")
print(f"Mã băm SHA-256 lưu trong DB: {api_key_hash}")