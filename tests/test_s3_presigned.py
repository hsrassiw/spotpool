import os
import unittest
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault('AWS_EC2_METADATA_DISABLED', 'true')

import boto3
from botocore.config import Config
from unittest.mock import patch
from services.api import helper


class PresignedUrlTests(unittest.TestCase):
    def test_browser_upload_and_download_use_regional_sigv4(self):
        self.assertEqual('s3v4', helper.s3_client.meta.config.signature_version)
        self.assertEqual('https://s3.ap-southeast-1.amazonaws.com', helper.s3_client.meta.endpoint_url)
        client = boto3.client('s3', region_name='ap-southeast-1', endpoint_url=helper.S3_ENDPOINT,
            config=Config(signature_version='s3v4'), aws_access_key_id='test', aws_secret_access_key='test')
        with patch.object(helper, 's3_client', client):
            for generate in (helper.generate_presigned_upload_url, helper.generate_presigned_download_url):
                url = urlsplit(generate('test-bucket', 'uploads/user/job/task', expiration=900))
                params = parse_qs(url.query)
                self.assertTrue(url.hostname.endswith('ap-southeast-1.amazonaws.com'))
                self.assertEqual(['AWS4-HMAC-SHA256'], params['X-Amz-Algorithm'])
                self.assertEqual(['900'], params['X-Amz-Expires'])
                self.assertNotIn('AWSAccessKeyId', params)
