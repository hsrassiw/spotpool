import boto3
from botocore.exceptions import ClientError

s3_client = boto3.client('s3', region_name='ap-southeast-1')

def generate_presigned_upload_url(bucket_name, object_name, expiration=3600):
    try:
        response = s3_client.generate_presigned_url('put_object',
                                                    Params={'Bucket': bucket_name,
                                                            'Key': object_name},
                                                    ExpiresIn=expiration)
    except ClientError as e:
        print(f"Lỗi tạo presigned URL upload: {e}")
        return None
    return response

def generate_presigned_download_url(bucket_name, object_name, expiration=900):
    try:
        response = s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': bucket_name,
                                                            'Key': object_name},
                                                    ExpiresIn=expiration)
    except ClientError as e:
        print(f"Lỗi tạo presigned URL download: {e}")
        return None
    return response