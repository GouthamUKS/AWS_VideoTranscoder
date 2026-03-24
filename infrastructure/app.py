#!/usr/bin/env python3
import aws_cdk as cdk

from stack import VideoTranscoderStack


app = cdk.App()
VideoTranscoderStack(app, "VideoTranscoderStack")

app.synth()
