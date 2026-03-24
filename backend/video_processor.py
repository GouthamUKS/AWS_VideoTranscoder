"""Lambda handler for video transcoding.

Triggered by S3 PutObject on uploads/ prefix.
Transcodes video to HLS (720p, 480p) and MP4 (720p).
Generates JPEG thumbnail.
Updates DynamoDB with job status.
"""

import json
import boto3
import subprocess
import os
import logging
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

SEGMENT_DURATION = 6
TABLE_NAME = os.environ.get('DYNAMODB_TABLE', 'VideoJobs')


def transcode_to_hls(input_path: str, output_dir: str, variant_name: str, 
                      width: int, height: int, bitrate: int) -> str:
    """Transcode video to HLS variant."""
    cmd = [
        'ffmpeg', '-i', input_path,
        '-vf', f'scale={width}:{height}',
        '-c:v', 'libx264',
        '-b:v', f'{bitrate}k',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-hls_time', str(SEGMENT_DURATION),
        '-hls_list_size', '0',
        f'{output_dir}/{variant_name}.m3u8'
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return f'{output_dir}/{variant_name}.m3u8'


def transcode_to_mp4(input_path: str, output_path: str) -> str:
    """Transcode video to web-optimized MP4."""
    cmd = [
        'ffmpeg', '-i', input_path,
        '-vf', 'scale=1280:720',
        '-c:v', 'libx264',
        '-b:v', '2500k',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-movflags', '+faststart',
        output_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def generate_thumbnail(input_path: str, output_path: str) -> str:
    """Generate JPEG thumbnail from 10-second mark."""
    cmd = [
        'ffmpeg', '-i', input_path,
        '-ss', '10',
        '-vframes', '1',
        output_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def update_dynamodb_status(job_id: str, status: str, output_keys: Dict[str, str] | None = None,
                          error_message: str | None = None) -> None:
    """Update job record in DynamoDB."""
    table = dynamodb.Table(TABLE_NAME)
    
    item: Dict[str, Any] = {
        'jobId': job_id,
        'status': status,
        'updatedAt': datetime.utcnow().isoformat(),
    }
    
    if output_keys:
        item['outputKeys'] = output_keys
    
    if error_message:
        item['errorMessage'] = error_message
    
    if status == 'COMPLETED' or status == 'FAILED':
        item['completedAt'] = datetime.utcnow().isoformat()
    
    table.put_item(Item=item)


def lambda_handler(event, context) -> Dict[str, Any]:
    """Process video transcoding job triggered by S3 event."""
    logger.info(f'Received event: {json.dumps(event)}')
    
    try:
        # Extract S3 bucket and key from event
        bucket = event['Records'][0]['s3']['bucket']['name']
        key = event['Records'][0]['s3']['object']['key']
        
        # Extract job ID from key (uploads/{jobId}.ext)
        job_id = key.split('/')[-1].split('.')[0]
        logger.info(f'Processing job {job_id}')
        
        # Update status to PROCESSING
        update_dynamodb_status(job_id, 'PROCESSING')
        
        # Download video from S3
        input_file = f'/tmp/{job_id}_input.mp4'
        s3_client.download_file(bucket, key, input_file)
        logger.info(f'Downloaded {key}')
        
        # Create output directory
        output_dir = f'/tmp/{job_id}_output'
        os.makedirs(output_dir, exist_ok=True)
        
        # Transcode to variants and MP4
        try:
            transcode_to_hls(input_file, output_dir, '720p', 1280, 720, 2500)
            transcode_to_hls(input_file, output_dir, '480p', 854, 480, 1200)
            
            mp4_path = f'{output_dir}/output.mp4'
            transcode_to_mp4(input_file, mp4_path)
            
            thumb_path = f'{output_dir}/thumb.jpg'
            generate_thumbnail(input_file, thumb_path)
            
            logger.info('Transcoding complete')
        except subprocess.CalledProcessError as e:
            logger.error(f'FFmpeg error: {str(e)}')
            update_dynamodb_status(job_id, 'FAILED', error_message=str(e))
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Transcoding failed'})
            }
        
        # Upload outputs to S3
        output_keys = {}
        base_key = f'outputs/{job_id}'
        
        # Upload HLS outputs
        for variant in ['720p', '480p']:
            variant_dir = f'{output_dir}/{variant}'
            if os.path.exists(variant_dir):
                for file in os.listdir(variant_dir):
                    s3_key = f'{base_key}/hls/{variant}/{file}'
                    s3_client.upload_file(f'{variant_dir}/{file}', bucket, s3_key)
                output_keys[f'hls_{variant}'] = s3_key
        
        # Upload MP4
        mp4_path = f'{output_dir}/output.mp4'
        if os.path.exists(mp4_path):
            s3_key = f'{base_key}/output.mp4'
            s3_client.upload_file(mp4_path, bucket, s3_key)
            output_keys['mp4'] = s3_key
        
        # Upload thumbnail
        thumb_path = f'{output_dir}/thumb.jpg'
        if os.path.exists(thumb_path):
            s3_key = f'{base_key}/thumb.jpg'
            s3_client.upload_file(thumb_path, bucket, s3_key)
            output_keys['thumbnail'] = s3_key
        
        logger.info(f'Uploaded outputs for job {job_id}')
        
        # Update DynamoDB with completion
        update_dynamodb_status(job_id, 'COMPLETED', output_keys)
        
        return {
            'statusCode': 200,
            'body': json.dumps({'jobId': job_id, 'status': 'COMPLETED'})
        }
    
    except Exception as e:
        logger.error(f'Unexpected error: {str(e)}')
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
