from urllib.parse import quote

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

def generate_presigned_download_url(bucket_name, object_name, expiration=900, filename=None, content_type=None):
    params = {'Bucket': bucket_name, 'Key': object_name}
    if content_type:
        params['ResponseContentType'] = content_type
    if filename:
        params['ResponseContentDisposition'] = content_disposition(filename)
    return s3_client.generate_presigned_url('get_object', Params=params, ExpiresIn=expiration)


def content_disposition(filename):
    """inline: trình duyệt mở được thì hiện luôn (văn bản, video), lưu về thì dùng đúng tên.
    filename= chỉ chứa ASCII an toàn cho trình duyệt cũ; filename*= giữ tên tiếng Việt (RFC 6266)."""
    ascii_name = ''.join(c if c.isascii() and (c.isalnum() or c in '._-') else '_' for c in filename)
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"
