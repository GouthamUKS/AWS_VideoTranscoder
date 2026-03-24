import os
import boto3
from aws_cdk import core as cdk
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3_deployment as s3deploy


class VideoTranscoderStack(cdk.Stack):
    """CDK Stack for serverless video transcoder."""

    def __init__(self, scope: cdk.Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account = cdk.Stack.of(self).account
        region = cdk.Stack.of(self).region

        # S3 Bucket for inputs and outputs
        bucket = s3.Bucket(
            self, "VideoTranscoderBucket",
            versioned=False,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            lifecycle_rules=[
                s3.LifecycleRule(
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=cdk.Duration.days(30),
                        )
                    ]
                )
            ],
            cors=[
                s3.CorsRule(
                    allowed_headers=["*"],
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.PUT, s3.HttpMethods.POST],
                    allowed_origins=["*"],
                    max_age=cdk.Duration.hours(1),
                )
            ],
        )

        # DynamoDB Table for job tracking
        jobs_table = dynamodb.Table(
            self, "VideoJobs",
            partition_key=dynamodb.Attribute(name="jobId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
        )

        # FFmpeg Lambda Layer (public, pre-built)
        ffmpeg_layer = lambda_.LayerVersion.from_layer_version_arn(
            self,
            "FFmpeg",
            f"arn:aws:lambda:{region}:496494173385:layer:FFMpeg:1",
        )

        # Video Processor Lambda
        video_processor = lambda_.Function(
            self, "VideoProcessor",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="video_processor.lambda_handler",
            code=lambda_.Code.from_asset("backend"),
            timeout=cdk.Duration.seconds(300),
            memory_size=1024,
            environment={
                "DYNAMODB_TABLE": jobs_table.table_name,
            },
            layers=[ffmpeg_layer],
        )

        # Permissions for Lambda to access S3 and DynamoDB
        bucket.grant_read_write(video_processor)
        jobs_table.grant_write_data(video_processor)

        # S3 trigger for video processor
        from aws_cdk import aws_s3_notifications as s3_notify
        bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3_notify.LambdaDestination(video_processor),
            s3.NotificationKeyFilter(prefix="uploads/"),
        )

        # Presigned URL Generator Lambda
        presigned_gen = lambda_.Function(
            self, "PresignedURLGenerator",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="presigned_url_generator.lambda_handler",
            code=lambda_.Code.from_asset("backend"),
            environment={
                "BUCKET_NAME": bucket.bucket_name,
            },
        )

        bucket.grant_read_write(presigned_gen)
        jobs_table.grant_read_data(presigned_gen)

        # API Gateway
        api = apigateway.RestApi(
            self, "VideoTranscoderAPI",
            rest_api_name="Video Transcoder API",
            description="API for video transcoding",
            default_cors_preflight_options={
                "allow_origins": apigateway.Cors.ALL_ORIGINS,
                "allow_methods": apigateway.Cors.ALL_METHODS,
            },
        )

        # POST /upload endpoint
        upload_resource = api.root.add_resource("upload")
        upload_resource.add_method(
            "POST",
            apigateway.LambdaIntegration(presigned_gen),
        )

        # GET /status/{jobId} endpoint
        status_resource = api.root.add_resource("status")
        job_resource = status_resource.add_resource("{jobId}")
        job_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(presigned_gen),
        )

        # GET /outputs/{jobId} endpoint
        outputs_resource = api.root.add_resource("outputs")
        output_job_resource = outputs_resource.add_resource("{jobId}")
        output_job_resource.add_method(
            "GET",
            apigateway.LambdaIntegration(presigned_gen),
        )

        # Outputs
        cdk.CfnOutput(self, "BucketName", value=bucket.bucket_name)
        cdk.CfnOutput(self, "ApiUrl", value=api.url)
        cdk.CfnOutput(self, "DynamoDBTable", value=jobs_table.table_name)
