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
import uuid
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


def update_dynamodb_status(job_id: str, status: str, output_keys: Dict[str, str] = None,
                          error_message: str = None) -> None:
    """Update job record in DynamoDB."""
    table = dynamodb.Table(TABLE_NAME)
    
    item = {
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
    """Process video transcoding job."""
    logger.info(f'Received event: {json.dumps(event)}')
    
    try:
        # Extract S3 event
        bucket = event['Records'][0]['s3']['bucket']['name']
        key = event['Records'][0]['s3']['object']['key']
        
        # Extract job ID from key (uploads/{jobId}.{ext})
        job_id = key.split('/')[-1].split('.')[0]
        
        # Update status to PROCESSING
        update_dynamodb_status(job_id, 'PROCESSING')
        
        # Download video from S3
        input_file = f'/tmp/{job_id}_input.mp4'
        s3_client.download_file(bucket, key, input_file)
        logger.info(f'Downloaded {key} to {input_file}')
        
        # Create output directory
        output_dir = f'/tmp/{job_id}_output'
        os.makedirs(output_dir, exist_ok=True)
        
        # Transcode
        try:
            # HLS variants
            transcode_to_hls(input_file, output_dir, '720p', 1280, 720, 2500)
            transcode_to_hls(input_file, output_dir, '480p', 854, 480, 1200)
            
            # MP4
            mp4_output = f'{output_dir}/output.mp4'
            transcode_to_mp4(input_file, mp4_output)
            
            # Thumbnail
            thumb_output = f'{output_dir}/thumb.jpg'
            generate_thumbnail(input_file, thumb_output)
            
            logger.info('Transcoding complete')
        except subprocess.CalledProcessError as e:
            logger.error(f'FFmpeg error: {e.stderr}')
            update_dynamodb_status(job_id, 'FAILED', error_message=str(e))
            return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
        
        # Upload outputs to S3
        output_keys = {}
        base_output_key = f'outputs/{job_id}'
        
        # Upload HLS files
        hls_dir = f'{output_dir}/720p'
        if os.path.exists(hls_dir):
            for file in os.listdir(hls_dir):
                s3_client.upload_file(
                    f'{hls_dir}/{file}',
                    bucket,
                    f'{base_output_key}/hls/{file}'
                )
            output_keys['hls'] = f'{base_output_key}/hls/720p.m3u8'
        
        # Upload MP4
        if os.path.exists(mp4_output):
            s3_client.upload_file(mp4_output, bucket, f'{base_output_key}/output.mp4')
            output_keys['mp4'] = f'{base_output_key}/output.mp4'
        
        # Upload thumbnail
        if os.path.exists(thumb_output):
            s3_client.upload_file(thumb_output, bucket, f'{base_output_key}/thumb.jpg')
            output_keys['thumbnail'] = f'{base_output_key}/thumb.jpg'
        
        # Update status to COMPLETED
        update_dynamodb_status(job_id, 'COMPLETED', output_keys=output_keys)
        logger.info(f'Job {job_id} completed')
        
        return {
            'statusCode': 200,
            'body': json.dumps({'jobId': job_id, 'status': 'COMPLETED'})
        }
    
    except Exception as e:
        logger.error(f'Error: {str(e)}')
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
