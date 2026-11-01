"""Admin-only fleet, queue, revenue and cached AWS cost overview (HM15)."""

import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

import boto3
from botocore.exceptions import ClientError

try:
    from .finance import response
except ImportError:
    from finance import response

logger = logging.getLogger(__name__)
VN = timezone(timedelta(hours=7))


def aws_cost(s3, ce, bucket, start, end, now):
    key = 'cache/aws-cost.json'
    cache = None
    try:
        cache = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
    except ClientError as error:
        if error.response['Error']['Code'] not in ('NoSuchKey', '404'):
            raise
    if (isinstance(cache, dict) and cache.get('end') == end
            and 0 <= now - cache.get('updatedAt', 0) < 86400):
        return cache, None
    try:
        costs = {}
        query = {'TimePeriod': {'Start': start, 'End': end}, 'Granularity': 'DAILY',
                 'Metrics': ['UnblendedCost'],
                 'Filter': {'Tags': {'Key': 'Project', 'Values': ['spotpool']}}}
        while True:
            page = ce.get_cost_and_usage(**query)
            for day in page.get('ResultsByTime', []):
                cost = day['Total']['UnblendedCost']
                if cost['Unit'] != 'USD':
                    raise ValueError('Cost Explorer returned an unsupported currency')
                costs[day['TimePeriod']['Start']] = int((Decimal(cost['Amount']) * 26000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            if not page.get('NextPageToken'):
                break
            query['NextPageToken'] = page['NextPageToken']
        cache = {'updatedAt': now, 'start': start, 'end': end, 'costs': costs}
        s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(cache).encode(), ContentType='application/json')
        return cache, None
    except Exception:
        logger.exception('Unable to refresh AWS costs')
        return cache or {'updatedAt': None, 'costs': {}}, 'AWS cost data is unavailable or stale'


def lambda_handler(event, context):
    auth = (event.get('requestContext') or {}).get('authorizer') or {}
    if not auth.get('userId'):
        return response(401, {'error': 'Unauthorized'})
    if auth.get('role') != 'admin':
        return response(403, {'error': 'Admin role required'})
    try:
        # Recheck the stored role so a revoked admin does not retain access through authorizer cache.
        db = boto3.resource('dynamodb', region_name='ap-southeast-1')
        user = db.Table(os.environ['USERS_TABLE']).get_item(Key={'userId': auth['userId']}, ConsistentRead=True).get('Item')
        if not user or user.get('role') != 'admin':
            return response(403, {'error': 'Admin role required'})
        ec2 = boto3.client('ec2', region_name='ap-southeast-1')
        counts = Counter()
        for page in ec2.get_paginator('describe_instances').paginate(Filters=[
                {'Name': 'tag:Project', 'Values': ['spotpool']},
                {'Name': 'instance-state-name', 'Values': ['running']} ]):
            for reservation in page['Reservations']:
                for instance in reservation['Instances']:
                    tier = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}.get('Tier')
                    if tier in ('ondemand', 'spot'):
                        counts[(tier, instance['InstanceType'], instance['Placement']['AvailabilityZone'])] += 1
        machines = [{'tier': tier, 'instanceType': kind, 'az': az, 'count': count}
                    for (tier, kind, az), count in sorted(counts.items())]
        sqs = boto3.client('sqs', region_name='ap-southeast-1')
        queues = {}
        for tier in ('ondemand', 'spot'):
            attributes = sqs.get_queue_attributes(QueueUrl=os.environ[f'SQS_{tier.upper()}_URL'],
                AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible'])['Attributes']
            queues[tier] = {'waiting': int(attributes['ApproximateNumberOfMessages']),
                            'running': int(attributes['ApproximateNumberOfMessagesNotVisible']),
                            'slots': 2 * sum(row['count'] for row in machines if row['tier'] == tier)}
        now = int(time.time())
        today = datetime.fromtimestamp(now, VN).date()
        days = [(today - timedelta(days=i)).isoformat() for i in reversed(range(7))]
        revenue = dict.fromkeys(days, 0)
        table = db.Table(os.environ['JOBS_TABLE'])
        scan = {'ProjectionExpression': 'settledAt, chargedCredit'}
        while True:
            page = table.scan(**scan)
            for job in page.get('Items', []):
                if 'settledAt' in job:
                    day = datetime.fromtimestamp(int(job['settledAt']), VN).date().isoformat()
                    if day in revenue:
                        revenue[day] += int(job.get('chargedCredit', 0))
            if not page.get('LastEvaluatedKey'):
                break
            scan['ExclusiveStartKey'] = page['LastEvaluatedKey']
        # Cost Explorer uses UTC calendar dates; revenue uses the project's VN business day.
        cache, warning = aws_cost(boto3.client('s3', region_name='ap-southeast-1'),
            boto3.client('ce', region_name='us-east-1'), os.environ['OUTPUT_BUCKET'],
            days[0], today.isoformat(), now)
        return response(200, {'machines': machines, 'queues': queues,
            'daily': [{'date': day, 'revenue': revenue[day], 'awsCost': cache['costs'].get(day)} for day in days],
            'awsCostUpdatedAt': cache['updatedAt'], 'awsCostWarning': warning,
            'awsCostTimezone': 'UTC', 'revenueTimezone': 'Asia/Ho_Chi_Minh', 'usdToCredit': 26000})
    except Exception:
        logger.exception('Unable to load admin overview')
        return response(500, {'error': 'Unable to load admin overview'})
