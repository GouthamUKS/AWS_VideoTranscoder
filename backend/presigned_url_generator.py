"""Lambda handler for presigned URL generation.

Generates presigned PUT URLs for frontend uploads.
Generates presigned GET URLs for output files.
"""

import json
import boto3
import logging
from typing import Dict, Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

BUCKET_NAME = os.environ.get('BUCKET_NAME', '')
PRESIGNED_URL_EXPIRATION = 3600  # 1 hour


def lambda_handler(event, context) -> Dict[str, Any]:
    """Generate presigned URLs."""
    logger.info(f'Received event: {json.dumps(event)}')
    
    try:
        body = json.loads(event.get('body', '{}'))
        action = body.get('action')
        key = body.get('key')
        
        if not action or not key:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing action or key'})
            }
        
        if action == 'putObject':
            # Generate presigned PUT URL for uploads
            url = s3_client.generate_presigned_url(
                'put_object',
                Params={'Bucket': BUCKET_NAME, 'Key': key},
                ExpiresIn=PRESIGNED_URL_EXPIRATION
            )
            
            return {
                'statusCode': 200,
                'body': json.dumps({'uploadUrl': url})
            }
        
        elif action == 'getObject':
            # Generate presigned GET URL for outputs
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': BUCKET_NAME, 'Key': key},
                ExpiresIn=PRESIGNED_URL_EXPIRATION
            )
            
            return {
                'statusCode': 200,
                'body': json.dumps({'downloadUrl': url})
            }
        
        else:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': f'Unknown action: {action}'})
            }
    
    except Exception as e:
        logger.error(f'Error: {str(e)}')
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
