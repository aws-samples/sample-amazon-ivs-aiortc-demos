#!/usr/bin/env python3
"""
SEI (Supplemental Enhancement Information) Publishing Package

This package provides tools for injecting SEI NAL units into H.264 video streams
to transmit metadata alongside video frames in real-time applications.

Key Components:
- SeiPublisher: High-level interface for publishing SEI messages
- H264SeiPatch: Low-level H.264 encoder patching for automatic SEI injection
- SeiMessage: Data structure for SEI message representation

SEI Message Identification:
- Uses custom UUID: b16d7d56-892e-419c-8d82-e069cd3aa5c1
- Embedded in H.264 SEI NAL units for client-side message identification

Usage:
    from stages_sei import SeiPublisher, patch_h264_encoder
    
    # Initialize SEI publisher
    sei_publisher = SeiPublisher()
    
    # Apply H.264 encoder patch
    patch_h264_encoder()
    
    # Publish SEI messages
    await sei_publisher.publish_json({"type": "metadata", "content": "Hello"})
"""

from .sei_publisher import SeiPublisher, SeiMessage
from .h264_sei_patch import patch_h264_encoder, set_global_sei_publisher, get_global_sei_publisher

__version__ = "1.0.0"
__author__ = "Amazon IVS Team"

__all__ = ["SeiPublisher", "SeiMessage", "patch_h264_encoder", "set_global_sei_publisher", "get_global_sei_publisher"]
