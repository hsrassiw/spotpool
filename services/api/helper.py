import boto3
from botocore.config import Config

S3_REGION = 'ap-southeast-1'
S3_ENDPOINT = f'https://s3.{S3_REGION}.amazonaws.com'
# Dùng endpoint theo region và SigV4 để S3 chấp nhận link PUT/GET từ trình duyệt.
s3_client = boto3.client(
    's3',
    region_name=S3_REGION,
    endpoint_url=S3_ENDPOINT,
    config=Config(signature_version='s3v4'),
)

def generate_presigned_upload_url(bucket_name, object_name, expiration=3600):
    return s3_client.generate_presigned_url(
        'put_object',
        Params={'Bucket': bucket_name, 'Key': object_name},
        ExpiresIn=expiration,
    )

def generate_presigned_download_url(bucket_name, object_name, expiration=900):
    return s3_client.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket_name, 'Key': object_name},
        ExpiresIn=expiration,
    )
