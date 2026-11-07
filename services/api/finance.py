"""Virtual credit deposits, budget increases, and personal invoices (HM14)."""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
dynamodb = boto3.resource('dynamodb', region_name='ap-southeast-1')
sqs = boto3.client('sqs', region_name='ap-southeast-1')
VN = timezone(timedelta(hours=7))
MESSAGE_FIELDS = ('schemaVersion', 'jobId', 'taskId', 'userId', 'tier', 'kind',
                  'vcpu', 'image', 'params', 'input', 'output')


def response(status, body):
    return {'statusCode': status, 'headers': {
        'Content-Type': 'application/json; charset=utf-8',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Authorization,Content-Type',
        'Cache-Control': 'no-store',
    }, 'body': json.dumps(body, default=lambda value: int(value) if isinstance(value, Decimal) else str(value))}


def identity(event):
    return ((event.get('requestContext') or {}).get('authorizer') or {}).get('userId')


def request_amount(event):
    body = json.loads(event.get('body') or '{}')
    if not isinstance(body, dict) or type(body.get('amount')) is not int or not 1 <= body['amount'] <= 100000000:
        raise ValueError('amount must be an integer between 1 and 100000000')
    request_id = body.get('requestId', str(uuid.uuid4()))
    if not isinstance(request_id, str):
        raise ValueError('requestId must be a UUID')
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, AttributeError):
        raise ValueError('requestId must be a UUID') from None
    return body['amount'], request_id


def query_all(table, **query):
    items = []
    while True:
        page = table.query(**query)
        items.extend(page.get('Items', []))
        if not page.get('LastEvaluatedKey'):
            return items
        query['ExclusiveStartKey'] = page['LastEvaluatedKey']


def _ledger(user_id, request_id, operation, amount, job_id=None):
    return {'userId': user_id, 'usageId': f'{operation}#{request_id}',
            'operation': operation, 'amount': amount, 'jobId': job_id or '',
            'createdAt': int(time.time())}


def _previous(entry):
    previous = dynamodb.Table(os.environ['USAGE_TABLE']).get_item(
        Key={key: entry[key] for key in ('userId', 'usageId')}, ConsistentRead=True,
    ).get('Item')
    if previous and (previous['amount'] != entry['amount'] or previous.get('jobId', '') != entry['jobId']):
        raise ValueError('requestId has already been used for a different request')
    return previous


def _put_ledger(entry):
    return {'Put': {'TableName': os.environ['USAGE_TABLE'], 'Item': entry,
                    'ConditionExpression': 'attribute_not_exists(usageId)'}}


def credits_handler(event, context):
    user_id = identity(event)
    if not user_id:
        return response(401, {'error': 'Unauthorized'})
    try:
        amount, request_id = request_amount(event)
        entry = _ledger(user_id, request_id, 'credit', amount)
        if not _previous(entry):
            try:
                dynamodb.meta.client.transact_write_items(TransactItems=[
                    {'Update': {'TableName': os.environ['USERS_TABLE'], 'Key': {'userId': user_id},
                                'UpdateExpression': 'ADD creditAvailable :amount',
                                'ConditionExpression': 'attribute_exists(userId)',
                                'ExpressionAttributeValues': {':amount': amount}}},
                    _put_ledger(entry),
                ])
            except ClientError as error:
                if error.response['Error']['Code'] != 'TransactionCanceledException' or not _previous(entry):
                    raise
        user = dynamodb.Table(os.environ['USERS_TABLE']).get_item(
            Key={'userId': user_id}, ConsistentRead=True)['Item']
        return response(200, {'requestId': request_id, 'creditAvailable': user['creditAvailable']})
    except (ValueError, TypeError) as error:
        return response(400, {'error': str(error)})
    except Exception:
        logger.exception('Unable to deposit virtual credit')
        return response(500, {'error': 'Unable to deposit virtual credit'})


def _resume(job, request_id):
    """Repeatable delivery: persist queued tasks before sending; retain pending on failure."""
    tasks = dynamodb.Table(os.environ['TASKS_TABLE'])
    for task in query_all(tasks, KeyConditionExpression=Key('jobId').eq(job['jobId']), ConsistentRead=True):
        if task['status'] == 'PAUSED':
            try:
                task = tasks.update_item(
                    Key={'jobId': job['jobId'], 'taskId': task['taskId']},
                    UpdateExpression='SET #s = :queued', ConditionExpression='#s = :paused',
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues={':queued': 'QUEUED', ':paused': 'PAUSED'},
                    ReturnValues='ALL_NEW',
                )['Attributes']
            except ClientError as error:
                if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    continue
                raise
        if task['status'] != 'QUEUED':
            continue
        message = {field: task[field] for field in MESSAGE_FIELDS}
        sqs.send_message(QueueUrl=os.environ['SQS_ONDEMAND_URL' if task['queue'] == 'ondemand' else 'SQS_SPOT_URL'],
                         MessageBody=json.dumps(message, default=lambda value: int(value) if value % 1 == 0 else float(value)))
    jobs = dynamodb.Table(os.environ['JOBS_TABLE'])
    try:
        jobs.update_item(
            Key={'jobId': job['jobId']}, UpdateExpression='REMOVE resumePending',
            ConditionExpression='resumePending = :request',
            ExpressionAttributeValues={':request': request_id},
        )
    except ClientError as error:
        if error.response['Error']['Code'] != 'ConditionalCheckFailedException':
            raise
        latest = jobs.get_item(Key={'jobId': job['jobId']}, ConsistentRead=True).get('Item', {})
        if latest.get('resumePending') == request_id:
            raise


def topup_handler(event, context):
    user_id = identity(event)
    if not user_id:
        return response(401, {'error': 'Unauthorized'})
    try:
        amount, request_id = request_amount(event)
        job_id = (event.get('pathParameters') or {}).get('id')
        jobs = dynamodb.Table(os.environ['JOBS_TABLE'])
        job = jobs.get_item(Key={'jobId': job_id}, ConsistentRead=True).get('Item') if job_id else None
        if not job or job.get('userId') != user_id:
            return response(404, {'error': 'Job not found'})
        entry = _ledger(user_id, request_id, 'topup', amount, job_id)
        if not _previous(entry):
            if job.get('resumePending'):
                return response(409, {'error': 'Retry the previous budget request to finish resuming tasks'})
            if job['status'] not in ('QUEUED', 'RUNNING', 'PAUSED') or 'settledAt' in job:
                return response(409, {'error': 'Job cannot receive more credit in its current state'})
            paused = job['status'] == 'PAUSED'
            if paused:
                tasks = query_all(dynamodb.Table(os.environ['TASKS_TABLE']),
                                  KeyConditionExpression=Key('jobId').eq(job_id), ConsistentRead=True)
                if any(task['status'] == 'RUNNING' for task in tasks):
                    return response(409, {'error': 'Wait for running tasks to pause, then retry'})
            values = {':amount': amount, ':cap': job['costCap'], ':status': job['status'], ':uid': user_id}
            update = 'SET costCap = costCap + :amount'
            if paused:
                update += ', #s = :queued, resumePending = :request'
                values.update({':queued': 'QUEUED', ':request': request_id})
            try:
                dynamodb.meta.client.transact_write_items(TransactItems=[
                    {'Update': {'TableName': os.environ['USERS_TABLE'], 'Key': {'userId': user_id},
                                'UpdateExpression': 'SET creditAvailable = creditAvailable - :amount, creditHeld = creditHeld + :amount',
                                'ConditionExpression': 'creditAvailable >= :amount',
                                'ExpressionAttributeValues': {':amount': amount}}},
                    {'Update': {'TableName': os.environ['JOBS_TABLE'], 'Key': {'jobId': job_id},
                                'UpdateExpression': update,
                                'ConditionExpression': '#s = :status AND costCap = :cap AND userId = :uid AND attribute_not_exists(settledAt) AND attribute_not_exists(resumePending)',
                                'ExpressionAttributeNames': {'#s': 'status'}, 'ExpressionAttributeValues': values}},
                    _put_ledger(entry),
                ])
            except ClientError as error:
                if error.response['Error']['Code'] != 'TransactionCanceledException':
                    raise
                if not _previous(entry):
                    return response(409, {'error': 'Insufficient credit or job changed; refresh and retry'})
        job = jobs.get_item(Key={'jobId': job_id}, ConsistentRead=True)['Item']
        if job.get('resumePending') == request_id:
            _resume(job, request_id)
        job = jobs.get_item(Key={'jobId': job_id}, ConsistentRead=True)['Item']
        return response(200, {'jobId': job_id, 'costCap': job['costCap'], 'status': job['status'], 'requestId': request_id})
    except (ValueError, TypeError) as error:
        return response(400, {'error': str(error)})
    except Exception:
        logger.exception('Unable to increase job budget')
        return response(500, {'error': 'Unable to increase job budget; retry with the same requestId'})


def invoices_handler(event, context):
    user_id = identity(event)
    if not user_id:
        return response(401, {'error': 'Unauthorized'})
    try:
        # The project-scale invoice view is scoped to this user, including all query pages.
        jobs = query_all(dynamodb.Table(os.environ['JOBS_TABLE']), IndexName='userId-createdAt-index',
                         KeyConditionExpression=Key('userId').eq(user_id), ScanIndexForward=False)
        today = datetime.now(VN).date()
        daily = {(today - timedelta(days=i)).isoformat(): {'vcpuSeconds': 0, 'chargedCredit': 0} for i in range(7)}
        invoices = []
        for job in jobs:
            if 'settledAt' not in job:
                continue
            invoice = {field: job.get(field, 0) for field in ('jobId', 'tier', 'unitPrice', 'usedVcpuSeconds',
                       'chargedCredit', 'costCap', 'settledAt')}
            invoice['billableVcpuSeconds'] = job.get('billableVcpuSeconds')
            invoices.append(invoice)
            day = datetime.fromtimestamp(int(job['settledAt']), VN).date().isoformat()
            if day in daily:
                daily[day]['chargedCredit'] += int(job['chargedCredit'])
        usage = query_all(dynamodb.Table(os.environ['USAGE_TABLE']), KeyConditionExpression=Key('userId').eq(user_id))
        for item in usage:
            if item.get('outcome') != 'INTERRUPTED' and item.get('day') in daily:
                daily[item['day']]['vcpuSeconds'] += int(item.get('vcpuSeconds', 0))
        return response(200, {'invoices': invoices, 'daily': [{'date': day, **daily[day]} for day in sorted(daily)]})
    except Exception:
        logger.exception('Unable to load invoices')
        return response(500, {'error': 'Unable to load invoices'})
