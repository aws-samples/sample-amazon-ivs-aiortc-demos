#!/usr/bin/env python3

import asyncio
import json
import logging
import argparse
import websockets
from websockets.exceptions import ConnectionClosed
import boto3
import subprocess
import sys
import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-assistant-manager")


class IVSStageAssistantManager:
    def __init__(self, chat_room_arn: str, ws_endpoint: str, max_instances: int = 5, region: str = "us-east-1", verbose: bool = False):
        self.chat_room_arn = chat_room_arn
        self.ws_endpoint = ws_endpoint
        self.max_instances = max_instances
        self.region = region
        self.verbose = verbose
        self.active_instances: Dict[str, subprocess.Popen] = {}
        self.instance_stage_arns: Dict[str, str] = {}  # Track stage ARN for each participant
        self.ivschat_client = boto3.client("ivschat", region_name=region)
        self.ivs_realtime_client = boto3.client("ivs-realtime", region_name=region)
        self.chat_token = None
        self.websocket = None
        self.manager_user_id = f"assistant-manager-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    async def generate_chat_token(self) -> str:
        """Generate a new chat token for connecting to the IVS chat room"""
        try:
            response = self.ivschat_client.create_chat_token(
                roomIdentifier=self.chat_room_arn,
                userId=self.manager_user_id,
                capabilities=["SEND_MESSAGE"],  # Only valid capabilities: SEND_MESSAGE, DISCONNECT_USER, DELETE_MESSAGE
            )

            token = response["token"]
            logger.info("✅ Generated new chat token successfully")
            return token

        except Exception as e:
            logger.error(f"❌ Failed to generate chat token: {e}")
            raise

    async def generate_stage_token(self, stage_arn: str, participant_id: str) -> str:
        """Generate a new stage participant token for the given stage ARN"""
        try:
            response = self.ivs_realtime_client.create_participant_token(
                stageArn=stage_arn,
                userId=f"nova-assistant-{participant_id}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
                capabilities=["PUBLISH", "SUBSCRIBE"],
                duration=720,  # 12 hours
            )

            token = response["participantToken"]["token"]
            logger.info(f"✅ Generated stage token for participant {participant_id}")
            return token

        except Exception as e:
            logger.error(f"❌ Failed to generate stage token for {stage_arn}: {e}")
            raise

    def get_sample_payload(self) -> Dict[str, Any]:
        """Return a sample JSON payload that the script expects"""
        return {"action": "LAUNCH_ASSISTANT", "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh", "participantId": "participant-123"}

    async def launch_nova_instance(self, stage_arn: str, participant_id: str) -> tuple[bool, str]:
        """Launch a new instance of the Nova S2S script"""
        if len(self.active_instances) >= self.max_instances:
            logger.warning(f"⚠️  Maximum instances ({self.max_instances}) reached. Cannot launch new instance for participant {participant_id}")
            return False, "MAX_INSTANCES_REACHED"

        if participant_id in self.active_instances:
            logger.warning(f"⚠️  Instance for participant {participant_id} already exists")
            return False, "INSTANCE_ALREADY_EXISTS"

        try:
            # Generate stage token for this participant
            logger.info(f"🎫 Generating stage token for participant {participant_id}")
            token = await self.generate_stage_token(stage_arn, participant_id)

            # Build command to launch the Nova S2S script
            script_path = os.path.join(os.path.dirname(__file__), "ivs-stage-nova-s2s.py")
            cmd = [sys.executable, script_path, "--token", token, "--subscribe-to", participant_id]

            logger.info(f"🚀 Launching Nova instance for participant {participant_id}")

            # Launch the process with appropriate output handling
            if self.verbose:
                # In verbose mode, capture output for streaming
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1)
            else:
                # In non-verbose mode, suppress output
                process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, universal_newlines=True)

            self.active_instances[participant_id] = process
            self.instance_stage_arns[participant_id] = stage_arn  # Store stage ARN for logging
            logger.info(f"✅ Nova instance launched for participant {participant_id} (PID: {process.pid})")
            logger.debug(f"🏷️  Stored stage ARN for {participant_id}: {stage_arn}")

            # Start monitoring the process
            asyncio.create_task(self.monitor_instance(participant_id, process))

            return True, "SUCCESS"

        except Exception as e:
            logger.error(f"❌ Failed to launch Nova instance for participant {participant_id}: {e}")
            return False, "LAUNCH_FAILED"

    async def monitor_instance(self, participant_id: str, process: subprocess.Popen):
        """Monitor a Nova instance and clean up when it exits"""
        try:
            if self.verbose and process.stdout:
                # Stream output in verbose mode
                logger.info(f"📺 Starting output streaming for participant {participant_id}")

                # Create tasks for output streaming and process monitoring
                stream_task = asyncio.create_task(self.stream_output(participant_id, process))

                # Create a proper coroutine for process waiting
                async def wait_for_process():
                    return await asyncio.get_event_loop().run_in_executor(None, process.wait)

                wait_task = asyncio.create_task(wait_for_process())

                # Wait for either the process to exit or streaming to complete
                done, pending = await asyncio.wait([stream_task, wait_task], return_when=asyncio.FIRST_COMPLETED)

                # Cancel any remaining tasks
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                # Get the return code
                if wait_task in done:
                    return_code = wait_task.result()
                else:
                    return_code = await asyncio.get_event_loop().run_in_executor(None, process.wait)
            else:
                # Non-verbose mode - just wait for process to complete
                return_code = await asyncio.get_event_loop().run_in_executor(None, process.wait)

            logger.info(f"🏁 Nova instance for participant {participant_id} exited with code {return_code}")

            # Clean up from active instances
            if participant_id in self.active_instances:
                del self.active_instances[participant_id]
                if participant_id in self.instance_stage_arns:
                    del self.instance_stage_arns[participant_id]
                logger.info(f"🧹 Cleaned up instance for participant {participant_id}")

        except Exception as e:
            logger.error(f"❌ Error monitoring instance for participant {participant_id}: {e}")
            # Ensure cleanup even on error
            if participant_id in self.active_instances:
                del self.active_instances[participant_id]
            if participant_id in self.instance_stage_arns:
                del self.instance_stage_arns[participant_id]

    async def stream_output(self, participant_id: str, process: subprocess.Popen):
        """Stream output from a Nova instance in real-time"""
        try:
            # Get the stage ARN for this participant (extract just the stage ID for brevity)
            stage_arn = self.instance_stage_arns.get(participant_id, "unknown-stage")
            stage_id = stage_arn.split("/")[-1] if "/" in stage_arn else stage_arn
            logger.debug(f"🔍 Stream output for {participant_id}: stage_arn={stage_arn}, stage_id={stage_id}")

            while True:
                line = await asyncio.get_event_loop().run_in_executor(None, process.stdout.readline)

                if not line:  # EOF
                    break

                # Print with stage and participant prefix
                line = line.rstrip("\n\r")
                if line:  # Only print non-empty lines
                    print(f"[{stage_id}::{participant_id}] {line}")

        except Exception as e:
            logger.error(f"❌ Error streaming output for participant {participant_id}: {e}")

    async def send_error_response(self, error_code: str, stage_arn: str, participant_id: str, message: str = ""):
        """Send an error response back through the WebSocket"""
        try:
            if self.websocket:
                # Extract stage ID from ARN for cleaner payload
                stage_id = stage_arn.split("/")[-1] if "/" in stage_arn else stage_arn

                error_response = {
                    "Action": "SEND_MESSAGE",
                    "Content": json.dumps({"error": error_code, "stageId": stage_id, "participantId": participant_id, "message": message}),
                }
                await self.websocket.send(json.dumps(error_response))
                logger.info(f"📤 Sent error response: {error_code} for {stage_id}::{participant_id}")
        except Exception as e:
            logger.error(f"❌ Failed to send error response: {e}")

    async def handle_message(self, message: str):
        """Handle incoming WebSocket message"""
        try:
            # Parse IVS Chat message wrapper
            chat_message = json.loads(message)
            logger.info(f"📨 Received chat message: {json.dumps(chat_message, indent=2)}")

            # Check if this is a MESSAGE type with Content
            if chat_message.get("Type") != "MESSAGE" or "Content" not in chat_message:
                logger.info("ℹ️  Ignoring non-MESSAGE or message without Content")
                return

            # Skip messages from the assistant manager itself (avoid processing our own error responses)
            sender_user_id = chat_message.get("Sender", {}).get("UserId")
            if sender_user_id == self.manager_user_id:
                logger.debug(f"🔄 Skipping message from assistant manager itself (UserId: {sender_user_id})")
                return

            # Parse the actual payload from Content field
            try:
                data = json.loads(chat_message["Content"])
                logger.info(f"📦 Parsed payload: {json.dumps(data, indent=2)}")
            except json.JSONDecodeError:
                logger.error("❌ Content field is not valid JSON")
                return

            # Validate required fields
            if "action" not in data or "stageArn" not in data or "participantId" not in data:
                logger.error("❌ Invalid message format. Missing 'action', 'stageArn', or 'participantId' fields")
                return

            action = data["action"]
            stage_arn = data["stageArn"]
            participant_id = data["participantId"]

            # Check if this is a launch assistant action
            if action != "LAUNCH_ASSISTANT":
                logger.info(f"ℹ️  Ignoring message with action '{action}' (expected 'LAUNCH_ASSISTANT')")
                return

            if not stage_arn or not participant_id:
                logger.error("❌ Empty stageArn or participantId values")
                return

            # Launch Nova instance
            success, error_code = await self.launch_nova_instance(stage_arn, participant_id)

            if success:
                logger.info(f"🎉 Successfully launched Nova instance for participant {participant_id}")
                logger.info(f"📊 Active instances: {len(self.active_instances)}/{self.max_instances}")
            else:
                logger.error(f"❌ Failed to launch Nova instance for participant {participant_id}: {error_code}")
                # Send error response back to the frontend
                await self.send_error_response(error_code, stage_arn, participant_id, f"Failed to launch assistant for participant {participant_id}")

        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON message: {e}")
        except Exception as e:
            logger.error(f"❌ Error handling message: {e}")

    async def connect_websocket(self):
        """Connect to the IVS chat WebSocket and listen for messages"""
        try:
            # Generate chat token
            self.chat_token = await self.generate_chat_token()

            # Connect to WebSocket using token as protocol (not query parameter)
            logger.info(f"🔌 Connecting to WebSocket: {self.ws_endpoint}")

            async with websockets.connect(self.ws_endpoint, subprotocols=[self.chat_token]) as websocket:
                self.websocket = websocket
                logger.info("✅ WebSocket connected and listening for messages")
                logger.info("📋 Expected message format:")
                print(json.dumps(self.get_sample_payload(), indent=2))

                async for message in websocket:
                    await self.handle_message(message)

        except ConnectionClosed:
            logger.warning("🔌 WebSocket connection closed")
        except Exception as e:
            logger.error(f"❌ WebSocket connection error: {e}")
            raise

    async def cleanup(self):
        """Clean up all active instances"""
        logger.info("🧹 Cleaning up active instances...")

        for participant_id, process in list(self.active_instances.items()):
            try:
                logger.info(f"🛑 Terminating instance for participant {participant_id}")
                process.terminate()

                # Wait for graceful shutdown
                try:
                    await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, process.wait), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(f"⚠️  Force killing instance for participant {participant_id}")
                    process.kill()

            except Exception as e:
                logger.error(f"❌ Error terminating instance for participant {participant_id}: {e}")

        self.active_instances.clear()
        self.instance_stage_arns.clear()
        logger.info("✅ Cleanup completed")

    async def run(self):
        """Main run loop"""
        try:
            await self.connect_websocket()
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down...")
        except Exception as e:
            logger.error(f"❌ Error in main loop: {e}")
        finally:
            await self.cleanup()


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="IVS Stage Assistant Manager - WebSocket listener for Nova S2S instances")
    parser.add_argument("--chat-room-arn", required=True, help="IVS Chat room ARN")
    parser.add_argument("--ws-endpoint", required=True, help="WebSocket endpoint URL")
    parser.add_argument("--max-instances", type=int, default=5, help="Maximum number of Nova instances (default: 5)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output from spawned Nova instances")

    return parser.parse_args()


async def main():
    """Main function"""
    args = parse_args()

    logger.info("🎬 Starting IVS Stage Assistant Manager")
    logger.info(f"📺 Chat Room ARN: {args.chat_room_arn}")
    logger.info(f"🔌 WebSocket Endpoint: {args.ws_endpoint}")
    logger.info(f"🔢 Max Instances: {args.max_instances}")
    logger.info(f"🌍 Region: {args.region}")
    logger.info(f"📢 Verbose Output: {'enabled' if args.verbose else 'disabled'}")

    # Create manager and log its user ID
    manager = IVSStageAssistantManager(
        chat_room_arn=args.chat_room_arn, ws_endpoint=args.ws_endpoint, max_instances=args.max_instances, region=args.region, verbose=args.verbose
    )
    logger.info(f"👤 Assistant Manager User ID: {manager.manager_user_id}")

    await manager.run()


if __name__ == "__main__":
    asyncio.run(main())
