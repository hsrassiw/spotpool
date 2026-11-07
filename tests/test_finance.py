import json
import os
import unittest
import uuid
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch

os.environ.setdefault('AWS_EC2_METADATA_DISABLED', 'true')
os.environ.setdefault('AWS_DEFAULT_REGION', 'ap-southeast-1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'testing')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'testing')

import boto3
from moto import mock_aws
from services.api import finance, admin_overview


class FinanceTests(unittest.TestCase):
    def test_resume_preserves_fractional_params(self):
        self.db.Table('Tasks').update_item(
            Key={'jobId': 'j1', 'taskId': 'task-0001'},
            UpdateExpression='SET params = :params',
            ExpressionAttributeValues={':params': {'threshold': Decimal('0.1'), 'nested': [Decimal('1.25')]}},
        )
        finance._resume({'jobId': 'j1'}, 'request')
        message = self.sqs.receive_message(QueueUrl=os.environ['SQS_ONDEMAND_URL'])['Messages'][0]
        self.assertEqual({'threshold': 0.1, 'nested': [1.25]}, json.loads(message['Body'])['params'])

    def setUp(self):
        self.aws = mock_aws()
        self.aws.start()
        self.addCleanup(self.aws.stop)
        self.env = patch.dict(os.environ, USERS_TABLE='Users', JOBS_TABLE='Jobs', TASKS_TABLE='Tasks', USAGE_TABLE='Usage')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = boto3.resource('dynamodb', region_name='ap-southeast-1')
        for name, keys in (('Users', ['userId']), ('Jobs', ['jobId']),
                           ('Tasks', ['jobId', 'taskId']), ('Usage', ['userId', 'usageId'])):
            fields = [{'AttributeName': key, 'AttributeType': 'S'} for key in keys]
            args = {}
            if name == 'Jobs':
                fields.extend([{'AttributeName': 'userId', 'AttributeType': 'S'}, {'AttributeName': 'createdAt', 'AttributeType': 'N'}])
                args['GlobalSecondaryIndexes'] = [{'IndexName': 'userId-createdAt-index',
                    'KeySchema': [{'AttributeName': 'userId', 'KeyType': 'HASH'}, {'AttributeName': 'createdAt', 'KeyType': 'RANGE'}],
                    'Projection': {'ProjectionType': 'ALL'}}]
            self.db.create_table(TableName=name, BillingMode='PAY_PER_REQUEST', AttributeDefinitions=fields,
                KeySchema=[{'AttributeName': key, 'KeyType': 'HASH' if i == 0 else 'RANGE'} for i, key in enumerate(keys)], **args)
        self.sqs = boto3.client('sqs', region_name='ap-southeast-1')
        os.environ['SQS_SPOT_URL'] = self.sqs.create_queue(QueueName='spot')['QueueUrl']
        os.environ['SQS_ONDEMAND_URL'] = self.sqs.create_queue(QueueName='ondemand')['QueueUrl']
        self.patch_db = patch.object(finance, 'dynamodb', self.db)
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)
        self.patch_sqs = patch.object(finance, 'sqs', self.sqs)
        self.patch_sqs.start()
        self.addCleanup(self.patch_sqs.stop)
        self.db.Table('Users').put_item(Item={'userId': 'u1', 'role': 'user', 'creditAvailable': 10000, 'creditHeld': 5000, 'runningJobs': 1})
        self.db.Table('Jobs').put_item(Item={'jobId': 'j1', 'userId': 'u1', 'status': 'PAUSED', 'costCap': 5000,
            'createdAt': 1, 'queuedAt': 1, 'unitPrice': 3000, 'tier': 'spot', 'usedVcpuSeconds': 6000})
        self.db.Table('Tasks').put_item(Item={
            'schemaVersion': 1, 'jobId': 'j1', 'taskId': 'task-0001', 'userId': 'u1',
            'tier': 'spot', 'kind': 'script', 'vcpu': 1, 'image': 'spotpool-script', 'params': {},
            'input': {'bucket': 'in', 'key': 'input'}, 'output': {'bucket': 'out', 'key': 'output'},
            'status': 'PAUSED', 'queue': 'ondemand', 'attempt': 3,
        })

    def event(self, amount=5000, request_id=None, user_id='u1'):
        return {'requestContext': {'authorizer': {'userId': user_id}}, 'pathParameters': {'id': 'j1'},
                'body': json.dumps({'amount': amount, 'requestId': request_id or str(uuid.uuid4())})}

    def user(self):
        return self.db.Table('Users').get_item(Key={'userId': 'u1'})['Item']

    def test_credit_deposit_is_idempotent_and_rejects_conflicting_reuse(self):
        event = self.event()
        for _ in range(2):
            self.assertEqual(200, finance.credits_handler(event, None)['statusCode'])
        self.assertEqual(15000, self.user()['creditAvailable'])
        body = json.loads(event['body'])
        body['amount'] = 1000
        event['body'] = json.dumps(body)
        self.assertEqual(400, finance.credits_handler(event, None)['statusCode'])
        self.assertEqual(15000, self.user()['creditAvailable'])

    def test_credit_validation_and_missing_identity(self):
        for amount in (True, 1.5, 0, -1, '5000', 100000001):
            self.assertEqual(400, finance.credits_handler(self.event(amount), None)['statusCode'])
        self.assertEqual(401, finance.credits_handler({}, None)['statusCode'])

    def test_topup_resumes_on_saved_queue_and_does_not_hold_twice(self):
        event = self.event()
        self.assertEqual(200, finance.topup_handler(event, None)['statusCode'])
        self.assertEqual(200, finance.topup_handler(event, None)['statusCode'])
        self.assertEqual(5000, self.user()['creditAvailable'])
        self.assertEqual(10000, self.user()['creditHeld'])
        job = self.db.Table('Jobs').get_item(Key={'jobId': 'j1'})['Item']
        self.assertEqual(10000, job['costCap'])
        self.assertEqual('QUEUED', job['status'])
        self.assertNotIn('resumePending', job)
        messages = self.sqs.receive_message(QueueUrl=os.environ['SQS_ONDEMAND_URL'])['Messages']
        self.assertEqual('spot', json.loads(messages[0]['Body'])['tier'])

    def test_failed_send_can_retry_without_second_credit_hold(self):
        event = self.event()
        with self.assertLogs(finance.logger, level='ERROR'), patch.object(finance.sqs, 'send_message', side_effect=RuntimeError('network unavailable')):
            self.assertEqual(500, finance.topup_handler(event, None)['statusCode'])
        self.assertEqual(200, finance.topup_handler(event, None)['statusCode'])
        self.assertEqual(5000, self.user()['creditAvailable'])
        self.assertEqual(10000, self.user()['creditHeld'])

    def test_topup_checks_ownership_balance_and_running_tasks(self):
        self.assertEqual(404, finance.topup_handler(self.event(user_id='other'), None)['statusCode'])
        self.assertEqual(409, finance.topup_handler(self.event(20000), None)['statusCode'])
        self.db.Table('Tasks').update_item(Key={'jobId': 'j1', 'taskId': 'task-0001'},
            UpdateExpression='SET #s=:s', ExpressionAttributeNames={'#s': 'status'}, ExpressionAttributeValues={':s': 'RUNNING'})
        self.assertEqual(409, finance.topup_handler(self.event(), None)['statusCode'])
        self.assertEqual(10000, self.user()['creditAvailable'])

    def test_personal_invoices_do_not_include_other_users(self):
        now = int(datetime.now(finance.VN).timestamp())
        self.db.Table('Jobs').update_item(Key={'jobId': 'j1'},
            UpdateExpression='SET settledAt=:t, chargedCredit=:c, billableVcpuSeconds=:s',
            ExpressionAttributeValues={':t': now, ':c': 5000, ':s': 6000})
        self.db.Table('Jobs').put_item(Item={'jobId': 'private', 'userId': 'other', 'createdAt': 2, 'settledAt': now, 'chargedCredit': 500})
        result = finance.invoices_handler(self.event(), None)
        self.assertEqual(200, result['statusCode'])
        data = json.loads(result['body'])
        self.assertEqual(['j1'], [row['jobId'] for row in data['invoices']])
        self.assertEqual(5000, sum(day['chargedCredit'] for day in data['daily']))

    def test_cancellation_waits_for_worker_usage_before_settlement(self):
        from services.api.billing import settle_job
        from services.api import cancel_job
        from worker.agent import Worker
        task = self.db.Table('Tasks').get_item(Key={'jobId': 'j1', 'taskId': 'task-0001'})['Item']
        message = {key: task[key] for key in finance.MESSAGE_FIELDS}
        self.db.Table('Tasks').update_item(Key={'jobId': 'j1', 'taskId': 'task-0001'},
            UpdateExpression='SET #s=:s, #o=:o', ExpressionAttributeNames={'#s': 'status', '#o': 'owner'},
            ExpressionAttributeValues={':s': 'RUNNING', ':o': 'worker'})
        self.db.Table('Jobs').update_item(Key={'jobId': 'j1'},
            UpdateExpression='SET #s=:s, tasksRemaining=:one, usedVcpuSeconds=:zero',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': 'RUNNING', ':one': 1, ':zero': 0})
        with patch.object(cancel_job, 'dynamodb', self.db):
            result = cancel_job.lambda_handler(self.event(), None)
        self.assertEqual(200, result['statusCode'])
        self.assertEqual('RUNNING', self.db.Table('Tasks').get_item(Key={'jobId': 'j1', 'taskId': 'task-0001'})['Item']['status'])
        names = {key: os.environ[key] for key in ('USERS_TABLE', 'JOBS_TABLE', 'TASKS_TABLE', 'USAGE_TABLE')}
        self.assertFalse(settle_job('j1', self.db, names))
        worker = Worker(dynamodb=self.db, sqs=Mock(), s3=Mock(),
            config={**names, 'SQS_SPOT_URL': 'spot', 'SQS_ONDEMAND_URL': 'ondemand'}, queue='spot')
        worker._finish(message, {'ReceiptHandle': 'receipt'}, 'worker', 3, 100, 3600,
                       'CANCELLED', None, 'log.txt', 'Cancelled')
        self.assertTrue(settle_job('j1', self.db, names))
        self.assertEqual(12000, self.user()['creditAvailable'])
        self.assertEqual(0, self.user()['creditHeld'])
        self.assertEqual(0, self.user()['runningJobs'])


class AdminTests(unittest.TestCase):
    def test_overview_groups_instances_and_reads_queue_and_revenue(self):
        from datetime import timezone
        now = int(datetime(2026, 11, 1, 5, tzinfo=timezone.utc).timestamp())
        db, users, jobs, ec2, sqs = Mock(), Mock(), Mock(), Mock(), Mock()
        db.Table.side_effect = lambda name: users if name == 'Users' else jobs
        users.get_item.return_value = {'Item': {'role': 'admin'}}
        jobs.scan.return_value = {'Items': [{'settledAt': now, 'chargedCredit': 3000}]}
        instance = {'InstanceType': 'c7i-flex.large', 'Placement': {'AvailabilityZone': 'ap-southeast-1a'},
                    'Tags': [{'Key': 'Tier', 'Value': 'spot'}]}
        ec2.get_paginator.return_value.paginate.return_value = [{'Reservations': [{'Instances': [instance, instance]}]}]
        sqs.get_queue_attributes.return_value = {'Attributes': {'ApproximateNumberOfMessages': '2', 'ApproximateNumberOfMessagesNotVisible': '1'}}
        clients = {'ec2': ec2, 'sqs': sqs, 's3': Mock(), 'ce': Mock()}
        with (patch.dict(os.environ, USERS_TABLE='Users', JOBS_TABLE='Jobs', OUTPUT_BUCKET='output',
                         SQS_SPOT_URL='spot', SQS_ONDEMAND_URL='ondemand'),
              patch.object(admin_overview.boto3, 'resource', return_value=db),
              patch.object(admin_overview.boto3, 'client', side_effect=lambda name, **kwargs: clients[name]),
              patch.object(admin_overview.time, 'time', return_value=now),
              patch.object(admin_overview, 'aws_cost', return_value=({'updatedAt': now, 'costs': {}}, None))):
            result = admin_overview.lambda_handler({'requestContext': {'authorizer': {'userId': 'u', 'role': 'admin'}}}, None)
        self.assertEqual(200, result['statusCode'])
        data = json.loads(result['body'])
        self.assertEqual(2, data['machines'][0]['count'])
        self.assertEqual({'waiting': 2, 'running': 1, 'slots': 4}, data['queues']['spot'])
        self.assertEqual(3000, data['daily'][-1]['revenue'])
        self.assertIsNone(data['daily'][-1]['awsCost'])

    def test_non_admin_cannot_access_aws(self):
        with patch.object(admin_overview.boto3, 'resource') as db:
            result = admin_overview.lambda_handler({'requestContext': {'authorizer': {'userId': 'u', 'role': 'user'}}}, None)
        self.assertEqual(403, result['statusCode'])
        db.assert_not_called()

    def test_cache_avoids_cost_explorer_calls_and_preserves_unknown_cost(self):
        import io
        cache = {'updatedAt': 100, 'end': '2026-11-01', 'costs': {'2026-10-31': 26000}}
        s3, ce = Mock(), Mock()
        s3.get_object.return_value = {'Body': io.BytesIO(json.dumps(cache).encode())}
        result, warning = admin_overview.aws_cost(s3, ce, 'output', '2026-10-26', '2026-11-01', 200)
        self.assertIsNone(warning)
        self.assertIsNone(result['costs'].get('2026-11-01'))
        ce.get_cost_and_usage.assert_not_called()

    def test_stale_cache_refreshes_and_converts_usd(self):
        import io
        s3, ce = Mock(), Mock()
        s3.get_object.return_value = {'Body': io.BytesIO(b'{"updatedAt": 1, "costs": {}}')}
        ce.get_cost_and_usage.return_value = {'ResultsByTime': [
            {'TimePeriod': {'Start': '2026-10-31'}, 'Total': {'UnblendedCost': {'Amount': '0.5', 'Unit': 'USD'}}}]}
        result, warning = admin_overview.aws_cost(s3, ce, 'output', '2026-10-26', '2026-11-01', 100000)
        self.assertIsNone(warning)
        self.assertEqual(13000, result['costs']['2026-10-31'])
        s3.put_object.assert_called_once()

    def test_revoked_admin_role_is_rechecked(self):
        db = Mock()
        db.Table.return_value.get_item.return_value = {'Item': {'role': 'user'}}
        with patch.dict(os.environ, USERS_TABLE='Users'), patch.object(admin_overview.boto3, 'resource', return_value=db):
            result = admin_overview.lambda_handler({'requestContext': {'authorizer': {'userId': 'u', 'role': 'admin'}}}, None)
        self.assertEqual(403, result['statusCode'])


if __name__ == '__main__':
    unittest.main()
