# worker

Worker agent chạy trên EC2 (Python 3.13): long polling SQS của gói ghi trong `SPOTPOOL_QUEUE`, giành task trên DynamoDB, heartbeat 30s, chạy 2 suất Docker cách ly, theo dõi cảnh báo thu hồi Spot qua IMDSv2. Phụ trách: TV2.
