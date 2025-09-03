#!/usr/bin/env python3

import re
import json
import time
import logging
import asyncio
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
import boto3
from botocore.exceptions import ClientError, BotoCoreError

logger = logging.getLogger(__name__)


class IVSMetadataPublisher:
    """
    A reusable module for publishing timed metadata to Amazon IVS channels.

    Handles rate limiting, payload size limits, and graceful error handling.
    """

    # IVS API limits
    MAX_PAYLOAD_SIZE = 1024  # 1 KB
    MAX_REQUESTS_PER_SECOND_PER_CHANNEL = 5
    MAX_REQUESTS_PER_SECOND_PER_ACCOUNT = 155

    def __init__(self, region: str = "us-east-1"):
        """
        Initialize the IVS metadata publisher

        Args:
            region: AWS region for IVS service
        """
        self.region = region
        self.ivs_client = None
        self.last_request_times = []  # Track request times for rate limiting
        self.channel_request_times = {}  # Track per-channel request times
        self._arn_cache = {}  # Cache playlist URL -> channel ARN mappings

        try:
            self.ivs_client = boto3.client("ivs", region_name=region)
            logger.info(f"🔗 IVS metadata publisher initialized for region: {region}")
        except Exception as e:
            logger.error(f"❌ Failed to initialize IVS client: {e}")
            self.ivs_client = None

    def extract_channel_arn_from_playlist_url(self, playlist_url: str) -> Optional[str]:
        """
        Extract channel ARN from M3U8 playlist URL with caching

        Args:
            playlist_url: M3U8 playlist URL

        Returns:
            Channel ARN string or None if extraction fails

        Example:
            Input: https://f99084460c35.us-east-1.playback.live-video.net/api/video/v1/us-east-1.639934345351.channel.x4aGUUxIp5Vw.m3u8
            Output: arn:aws:ivs:us-east-1:639934345351:channel/x4aGUUxIp5Vw
        """
        # Check cache first
        if playlist_url in self._arn_cache:
            logger.debug(f"🎯 Using cached ARN for {playlist_url}")
            return self._arn_cache[playlist_url]

        try:
            # Parse the URL
            parsed_url = urlparse(playlist_url)

            # Extract path components
            # Expected format: /api/video/v1/us-east-1.639934345351.channel.x4aGUUxIp5Vw.m3u8
            path = parsed_url.path

            # Use regex to extract region, account ID, and channel ID
            pattern = r"/api/video/v1/([^.]+)\.(\d+)\.channel\.([^.]+)\.m3u8"
            match = re.search(pattern, path)

            if not match:
                logger.error(f"❌ Could not parse playlist URL format: {playlist_url}")
                return None

            region, account_id, channel_id = match.groups()

            # Construct ARN
            arn = f"arn:aws:ivs:{region}:{account_id}:channel/{channel_id}"

            # Cache the result
            self._arn_cache[playlist_url] = arn
            logger.info(f"✅ Extracted and cached channel ARN: {arn}")
            return arn

        except Exception as e:
            logger.error(f"❌ Error extracting channel ARN from URL: {e}")
            return None

    def _check_rate_limits(self, channel_arn: str) -> bool:
        """
        Check if we can make a request without exceeding rate limits

        Args:
            channel_arn: Channel ARN for per-channel rate limiting

        Returns:
            True if request can be made, False if rate limited
        """
        current_time = time.time()

        # Clean old request times (older than 1 second)
        self.last_request_times = [t for t in self.last_request_times if current_time - t < 1.0]

        # Check account-wide rate limit (155 requests per second)
        if len(self.last_request_times) >= self.MAX_REQUESTS_PER_SECOND_PER_ACCOUNT:
            logger.warning("⚠️  Account-wide rate limit reached (155 RPS)")
            return False

        # Check per-channel rate limit (5 requests per second)
        if channel_arn not in self.channel_request_times:
            self.channel_request_times[channel_arn] = []

        # Clean old channel request times
        self.channel_request_times[channel_arn] = [t for t in self.channel_request_times[channel_arn] if current_time - t < 1.0]

        if len(self.channel_request_times[channel_arn]) >= self.MAX_REQUESTS_PER_SECOND_PER_CHANNEL:
            logger.warning(f"⚠️  Channel rate limit reached (5 RPS) for {channel_arn}")
            return False

        return True

    def _record_request(self, channel_arn: str) -> None:
        """Record a request for rate limiting purposes"""
        current_time = time.time()
        self.last_request_times.append(current_time)

        if channel_arn not in self.channel_request_times:
            self.channel_request_times[channel_arn] = []
        self.channel_request_times[channel_arn].append(current_time)

    def _split_payload(self, metadata: str) -> List[str]:
        """
        Split metadata into chunks that fit within the 1KB limit

        Args:
            metadata: Metadata string to split

        Returns:
            List of metadata chunks
        """
        if len(metadata.encode("utf-8")) <= self.MAX_PAYLOAD_SIZE:
            return [metadata]

        chunks = []
        current_chunk = ""

        # Split by words to avoid breaking in the middle of words
        words = metadata.split()

        for word in words:
            test_chunk = current_chunk + (" " if current_chunk else "") + word

            if len(test_chunk.encode("utf-8")) > self.MAX_PAYLOAD_SIZE:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = word
                else:
                    # Single word is too large, split it
                    while len(word.encode("utf-8")) > self.MAX_PAYLOAD_SIZE:
                        split_point = self.MAX_PAYLOAD_SIZE
                        while split_point > 0 and len(word[:split_point].encode("utf-8")) > self.MAX_PAYLOAD_SIZE:
                            split_point -= 1

                        chunks.append(word[:split_point])
                        word = word[split_point:]

                    current_chunk = word
            else:
                current_chunk = test_chunk

        if current_chunk:
            chunks.append(current_chunk)

        logger.info(f"📦 Split metadata into {len(chunks)} chunks")
        return chunks

    async def publish_metadata(self, channel_arn: str, metadata: str, metadata_type: str = "transcript") -> bool:
        """
        Publish metadata to an IVS channel with rate limiting and error handling

        Args:
            channel_arn: IVS channel ARN
            metadata: Metadata content to publish
            metadata_type: Type of metadata (for logging purposes)

        Returns:
            True if successful, False if failed
        """
        if not self.ivs_client:
            logger.error("❌ IVS client not initialized")
            return False

        if not metadata.strip():
            logger.warning("⚠️  Empty metadata, skipping publish")
            return True

        try:
            # Split metadata if it exceeds size limit
            chunks = self._split_payload(metadata)

            success_count = 0
            total_chunks = len(chunks)

            for i, chunk in enumerate(chunks):
                # Check rate limits
                while not self._check_rate_limits(channel_arn):
                    logger.info("⏳ Rate limit reached, waiting 1 second...")
                    await asyncio.sleep(1.0)

                try:
                    # Prepare metadata payload
                    if total_chunks > 1:
                        # Add chunk information for multi-part messages
                        chunk_metadata = f"[{i+1}/{total_chunks}] {chunk}"
                    else:
                        chunk_metadata = chunk

                    # Call IVS PutMetadata API
                    response = self.ivs_client.put_metadata(channelArn=channel_arn, metadata=chunk_metadata)

                    # Record the request for rate limiting
                    self._record_request(channel_arn)

                    logger.info(f"✅ Published {metadata_type} metadata chunk {i+1}/{total_chunks} ({len(chunk_metadata)} bytes)")
                    success_count += 1

                    # Small delay between chunks to avoid overwhelming the API
                    if i < total_chunks - 1:
                        await asyncio.sleep(0.2)

                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "Unknown")
                    error_message = e.response.get("Error", {}).get("Message", str(e))

                    if error_code == "ThrottlingException":
                        logger.warning(f"⚠️  Throttled on chunk {i+1}, waiting and retrying...")
                        await asyncio.sleep(2.0)
                        # Retry this chunk
                        try:
                            response = self.ivs_client.put_metadata(channelArn=channel_arn, metadata=chunk_metadata)
                            self._record_request(channel_arn)
                            logger.info(f"✅ Retry successful for chunk {i+1}/{total_chunks}")
                            success_count += 1
                        except Exception as retry_error:
                            logger.error(f"❌ Retry failed for chunk {i+1}: {retry_error}")

                    elif error_code == "ResourceNotFoundException":
                        logger.error(f"❌ Channel not found: {channel_arn}")
                        return False

                    elif error_code == "ChannelNotBroadcasting":
                        logger.warning(f"⚠️  Channel not currently broadcasting: {channel_arn}")
                        return False

                    else:
                        logger.error(f"❌ AWS API error on chunk {i+1}: {error_code} - {error_message}")

                except BotoCoreError as e:
                    logger.error(f"❌ AWS connection error on chunk {i+1}: {e}")

                except Exception as e:
                    logger.error(f"❌ Unexpected error on chunk {i+1}: {e}")

            # Return success if at least one chunk was published
            if success_count > 0:
                logger.info(f"🎉 Successfully published {success_count}/{total_chunks} {metadata_type} chunks")
                return True
            else:
                logger.error(f"❌ Failed to publish any {metadata_type} chunks")
                return False

        except Exception as e:
            logger.error(f"❌ Error publishing {metadata_type} metadata: {e}")
            return False

    async def publish_transcript(self, playlist_url: str, transcript: str) -> bool:
        """
        Convenience method to publish transcript metadata

        Args:
            playlist_url: M3U8 playlist URL
            transcript: Transcript text to publish (caller handles formatting)

        Returns:
            True if successful, False if failed
        """
        # Extract channel ARN (uses cache if available)
        channel_arn = self.extract_channel_arn_from_playlist_url(playlist_url)
        if not channel_arn:
            return False

        return await self.publish_metadata(channel_arn, transcript, "transcript")


# Example usage
if __name__ == "__main__":
    import asyncio

    async def test_publisher():
        # Test URL
        test_url = "https://f99084460c35.us-east-1.playback.live-video.net/api/video/v1/us-east-1.639934345351.channel.x4aGUUxIp5Vw.m3u8"

        publisher = IVSMetadataPublisher()

        # Test ARN extraction
        arn = publisher.extract_channel_arn_from_playlist_url(test_url)
        print(f"Extracted ARN: {arn}")

        # Test transcript publishing
        test_transcript = "Hello, this is a test transcript from the IVS metadata publisher."
        success = await publisher.publish_transcript(test_url, test_transcript)
        print(f"Publish success: {success}")

    # Run test
    # asyncio.run(test_publisher())
