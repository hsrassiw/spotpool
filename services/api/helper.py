import boto3
from botocore.exceptions import ClientError

# Khởi tạo S3 client cho region Singapore (ap-southeast-1)
s3_client = boto3.client('s3', region_name='ap-southeast-1')

def generate_presigned_upload_url(bucket_name, object_name, expiration=3600):
    """
    Tạo presigned URL để người dùng tải file đầu vào trực tiếp lên S3 bucket.
    """
    try:
        response = s3_client.generate_presigned_url('put_object',
                                                    Params={'Bucket': bucket_name,
                                                            'Key': object_name},
                                                    ExpiresIn=expiration)
    except ClientError as e:
        print(f"Lỗi khi tạo presigned URL upload: {e}")
        return None
    return response

def generate_presigned_download_url(bucket_name, object_name, expiration=604800):
    """
    Tạo presigned URL để người dùng tải kết quả về (giữ mặc định 7 ngày = 604800 giây).
    """
    try:
        response = s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': bucket_name,
                                                            'Key': object_name},
                                                    ExpiresIn=expiration)
    except ClientError as e:
        print(f"Lỗi khi tạo presigned URL download: {e}")
        return None
    return response