# worker

Worker agent chạy trên EC2: long polling SQS, giành job trên DynamoDB, heartbeat 30s, chạy 2 suất Docker cách ly, theo dõi cảnh báo thu hồi Spot qua IMDSv2. Phụ trách: TV2.
