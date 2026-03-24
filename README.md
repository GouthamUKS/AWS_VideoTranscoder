# AWS Serverless Video Transcoder

A serverless video encoding pipeline on AWS using Lambda, S3, and DynamoDB. Transcodes input videos to multiple formats (HLS, MP4) with automatic quality variants, thumbnail generation, and progress tracking.

## Architecture

```
     Browser
       |
       v
  [Presigned URL API] (API Gateway)
       |
       +----> [Presigned URL Lambda] -> DynamoDB (job records)
       |                                   ^
       v                                   |
  [Upload to S3]                           |
  (uploads/ prefix)                        |
       |                                   |
       v                                   |
  [S3 Event] --triggers--> [Video Processor Lambda]
                                 |
                                 +----> [FFmpeg Transcode]
                                 :       - HLS 720p
                                 :       - HLS 480p
                                 :       - MP4 720p (web-optimized)
                                 :       - JPEG thumbnail
                                 |
                                 v
                           [Upload to S3]
                           (outputs/ prefix)
                                 |
                                 v
                           [Update DynamoDB]
                           (status=COMPLETED)
                                 |
                                 v
                           [Browser polls /status/{jobId}]
                                 |
                                 v
                           [Gets presigned URLs]
                           [Plays HLS/MP4]
```

## AWS Services Used

- **S3**: Input and output video storage
- **Lambda**: Compute for video transcoding (video-processor, presigned-url-generator)
- **DynamoDB**: Job metadata and status tracking
- **API Gateway**: REST API endpoints
- **CloudWatch**: Logging and monitoring
- **IAM**: Permissions management
- **SQS** (optional): Job queue for decoupling

Within **AWS Free Tier**:
- Lambda: 1M free invocations/month (you'll use ~10-20)
- S3: 5GB free storage (delete outputs after testing)
- DynamoDB: 25GB free storage (minimal usage, ~1KB per job)
- API Gateway: 1M free calls/month
- No MediaConvert charges - using FFmpeg in Lambda instead

## Project Structure

```
aws-video-transcoder/
├── infrastructure/
│   ├── bin/
│   │   └── app.ts                 # CDK app entry point
│   ├── lib/
│   │   └── video-transcoder-stack.ts  # CDK stack definition
│   ├── package.json
│   ├── tsconfig.json
│   └── cdk.json
│
├── backend/
│   ├── video-processor/
│   │   ├── lambda_handler.py      # Video transcoding Lambda
│   │   └── requirements.txt        # Python dependencies
│   └── presigned-url/
│       ├── lambda_handler.py      # Presigned URL generator
│       └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── Upload.tsx          # Drag-and-drop upload
│   │   │   ├── JobStatus.tsx       # Status polling
│   │   │   └── Player.tsx          # HLS.js player
│   │   ├── services/
│   │   │   └── api.ts             # API client
│   │   └── types/
│   │       └── index.ts
│   ├── package.json
│   ├── vite.config.ts
│   └── tailwind.config.js
│
├── ARCHITECTURE.md                 # Detailed architecture docs
├── README.md
└── .gitignore
```

## Prerequisites

- AWS Account with valid payment method (for billing alerts)
- AWS CLI configured with credentials
- Node.js 18+ and npm
- Python 3.11+

### Critical: Set AWS Billing Alert

Before deploying, create a $1 budget alert:

```bash
# AWS Console > Billing > Budgets > Create budget
# Or via AWS CLI:
aws budgets create-budget \
  --account-id $(aws sts get-caller-identity --query Account --output text) \
  --budget file://budget.json \
  --notifications-with-subscribers file://notifications.json
```

This prevents runaway costs if something goes wrong.

## Setup Instructions

### 1. Infrastructure Setup

```bash
cd infrastructure
npm install

# First time only - sets up CDK toolkit in your AWS account
npx cdk bootstrap

# View what will be deployed
npx cdk diff

# Deploy to AWS
npx cdk deploy

# Note outputs - they're needed for frontend configuration:
# - ApiUrl: https://xxxxx.execute-api.us-east-1.amazonaws.com
# - S3BucketName: video-transcoder-uploads-ACCOUNT
# - DynamoDBTableName: VideoJobs
```

### 2. Lambda Layer Setup

The video-processor Lambda needs FFmpeg. Options:

**Option A: Public FFmpeg Layer** (recommended for demo, avoid charges)

The CDK stack references a public FFmpeg layer:
```typescript
const ffmpegLayer = lambda.LayerVersion.fromLayerVersionArn(
  this,
  'FFmpegLayer',
  `arn:aws:lambda:${this.region}:${this.account}:layer:ffmpeg-${this.region}:2`
);
```

**Option B: Private Layer** (for production)

Build and upload custom FFmpeg:
```bash
# Download FFmpeg binary
pip install aws-cdk-lib
# Create layer with ffmpeg binary in python/lib/python3.11/site-packages/
# Upload as layer version
```

### 3. Backend Deployment

CDK automatically packages Lambda functions from `/backend` and deploys them.

The `video-processor` Lambda:
- Triggered by S3 PutObject on `uploads/` prefix
- Downloads video from S3 to `/tmp`
- Runs FFmpeg commands
- Uploads results to `outputs/{jobId}/`
- Updates DynamoDB status

The `presigned-url-generator` Lambda:
- Generates signed PUT URLs for frontend uploads
- Generates signed GET URLs for output objects
- Returns URLs to frontend for direct S3 access

### 4. Frontend Setup

```bash
cd frontend
npm install

# Create .env with API URL from CDK output
echo "VITE_API_URL=https://xxxxx.execute-api.region.amazonaws.com" > .env

# Test locally
npm run dev
# Visit http://localhost:5173

# Build for production
npm run build

# Deploy to Vercel (optional)
npm install -g vercel
vercel --prod
```

## Usage

### Web Browser

1. Open https://yourapp.vercel.app (or http://localhost:5173 in dev)
2. Drag-and-drop or select a video file (MP4, MOV, WebM)
3. Click "Upload"
4. Watch progress bar
5. When ready, click "Play" to watch HLS stream or download MP4
6. Click "Get Thumbnail" to download JPEG preview

### Testing Locally

```bash
# Terminal 1: Start frontend dev server
cd frontend
npm run dev

# Terminal 2: (After deploy) Test with AWS CLI
VIDEO_FILE=test.mp4
JOB_ID=$(uuidgen)

# Generate upload URL
UPLOAD_URL=$(aws lambda invoke --function-name presigned-url-generator \
  --payload '{"action":"putObject","key":"uploads/'$JOB_ID'.mp4"}' \
  --query 'Payload.uploadUrl' \
  response.json | tr -d '"')

# Upload video
curl -X PUT -d @$VIDEO_FILE --content-type video/mp4 "$UPLOAD_URL"

# Poll job status
aws dynamodb get-item \
  --table-name VideoJobs \
  --key '{"jobId":{"S":"'$JOB_ID'"}}'
```

## Cost Estimates

For 10 test videos (1 minute each):

- Lambda invocations: ~$0 (within free tier of 1M/month)
- S3 storage (7-day lifecycle): ~$0.01 (minimal during cleanup)
- DynamoDB: ~$0 (under 25GB free tier)
- **Total**: < $0.05

As long as you delete videos after testing, you'll stay within free tier.

## Cleanup (CRITICAL - Avoid Charges)

DELETE resources after testing:

```bash
# Destroy all AWS infrastructure
cd infrastructure
npx cdk destroy

# Confirm deletion

# Also manually delete S3 bucket if cdk destroy fails:
aws s3 rm s3://video-transcoder-uploads-ACCOUNT --recursive
aws s3 rb s3://video-transcoder-uploads-ACCOUNT
```

Not cleaning up can result in unexpected charges from idle resources.

## Author

Built by Goutham Soratoor, extending the same serverless architecture from **AI Document Processor** into video encoding. That project uses Lambda, S3, DynamoDB, and CDK for document processing - this applies the same patterns to video.

GitHub: https://github.com/GouthamUKS
Vercel: https://vercel.com/gouthamukss-projects

## Implementation Details

### FFmpeg Commands Used

```bash
# HLS 720p variant
ffmpeg -i input.mp4 \
  -vf scale=1280:720 \
  -c:v libx264 -b:v 2500k \
  -c:a aac -b:a 128k \
  -hls_time 6 -hls_list_size 0 \
  output_720p.m3u8

# MP4 web-optimized (fast start)
ffmpeg -i input.mp4 \
  -vf scale=1280:720 \
  -c:v libx264 -b:v 2500k \
  -c:a aac -b:a 128k \
  -movflags +faststart \
  output.mp4

# Thumbnail at 10 seconds
ffmpeg -i input.mp4 -ss 10 -vframes 1 thumb.jpg
```

### DynamoDB Schema

```
TableName: VideoJobs
PartitionKey: jobId (String)

Attributes:
- jobId (PK)
- status (PENDING, PROCESSING, COMPLETED, FAILED)
- inputKey (s3://bucket/uploads/...)
- outputKeys (map: {hls, mp4, thumbnail})
- createdAt (timestamp)
- completedAt (timestamp)
- errorMessage (string, if FAILED)
```

### API Endpoints

```
POST /upload
  Request: { action: "putObject", key: "uploads/VIDEO.mp4", maxSize: 100000000 }
  Response: { uploadUrl: "https://s3.../..." }

GET /status/{jobId}
  Response: {
    jobId: "",
    status: "COMPLETED",
    outputKeys: { hls: "...", mp4: "...", thumbnail: "..." },
    createdAt: "...",
    completedAt: "..."
  }

GET /outputs/{jobId}
  Response: {
    hlsUrl: "presigned GET URL for .m3u8",
    mp4Url: "presigned GET URL for .mp4",
    thumbnailUrl: "presigned GET URL for .jpg"
  }
```

## Notes

- All source videos stored in S3 are deleted after 7 days (lifecycle rule) to contain storage costs
- Lambda timeout set to 300 seconds (5 minutes) - appropriate for ~1 minute videos
- Memory set to 1024MB for transcoding performance
- Frontend handles stalled uploads with exponential backoff
- Status polling every 3 seconds (not constant)

## License

MIT License
