#!/usr/bin/env python3
"""
IVS Stage Deepgram Agent Manager

Listens on an IVS Chat WebSocket for LAUNCH_ASSISTANT messages and spawns
Deepgram Voice Agent instances (ivs-stage-deepgram-agent.py) for each
participant. Supports configurable voice, LLM provider/model, prompt,
and greeting via the chat message payload.
"""

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
from typing import Dict, Any, Optional
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ivs-stage-deepgram-agent-manager")


class IVSStageDeepgramAgentManager:
    def __init__(
        self,
        chat_room_arn: str,
        ws_endpoint: str,
        deepgram_api_key: str,
        max_instances: int = 5,
        region: str = "us-east-1",
        verbose: bool = False,
    ):
        self.chat_room_arn = chat_room_arn
        self.ws_endpoint = ws_endpoint
        self.deepgram_api_key = deepgram_api_key
        self.max_instances = max_instances
        self.region = region
        self.verbose = verbose
        self.active_instances: Dict[str, subprocess.Popen] = {}
        self.instance_stage_arns: Dict[str, str] = {}
        self.ivschat_client = boto3.client("ivschat", region_name=region)
        self.ivs_realtime_client = boto3.client("ivs-realtime", region_name=region)
        self.chat_token = None
        self.websocket = None
        self.manager_user_id = f"deepgram-agent-manager-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    async def generate_chat_token(self) -> str:
        try:
            response = self.ivschat_client.create_chat_token(
                roomIdentifier=self.chat_room_arn,
                userId=self.manager_user_id,
                capabilities=["SEND_MESSAGE"],
            )
            token = response["token"]
            logger.info("✅ Generated new chat token successfully")
            return token
        except Exception as e:
            logger.error(f"❌ Failed to generate chat token: {e}")
            raise

    async def generate_stage_token(self, stage_arn: str, participant_id: str) -> str:
        try:
            response = self.ivs_realtime_client.create_participant_token(
                stageArn=stage_arn,
                userId=f"deepgram-agent-{participant_id}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
                capabilities=["PUBLISH", "SUBSCRIBE"],
                duration=720,
            )
            token = response["participantToken"]["token"]
            logger.info(f"✅ Generated stage token for participant {participant_id}")
            return token
        except Exception as e:
            logger.error(f"❌ Failed to generate stage token for {stage_arn}: {e}")
            raise

    def get_sample_payload(self) -> Dict[str, Any]:
        return {
            "action": "LAUNCH_ASSISTANT",
            "stageArn": "arn:aws:ivs:us-east-1:123456789012:stage/abcdefgh",
            "participantId": "participant-123",
            "voice": "aura-2-asteria-en",
            "thinkProvider": "open_ai",
            "thinkModel": "gpt-4o-mini",
            "prompt": "You are a friendly, helpful voice assistant.",
            "greeting": "Hello! How can I help you today?",
            "language": "en",
        }

    async def launch_deepgram_agent_instance(self, stage_arn: str, participant_id: str, config: Dict[str, Any] = None) -> tuple[bool, str]:
        if len(self.active_instances) >= self.max_instances:
            logger.warning(f"⚠️  Maximum instances ({self.max_instances}) reached")
            return False, "MAX_INSTANCES_REACHED"

        if participant_id in self.active_instances:
            logger.warning(f"⚠️  Instance for participant {participant_id} already exists")
            return False, "INSTANCE_ALREADY_EXISTS"

        try:
            logger.info(f"🎫 Generating stage token for participant {participant_id}")
            token = await self.generate_stage_token(stage_arn, participant_id)

            script_path = os.path.join(os.path.dirname(__file__), "ivs-stage-deepgram-agent.py")
            cmd = [
                sys.executable,
                script_path,
                "--token",
                token,
                "--subscribe-to",
                participant_id,
                "--deepgram-api-key",
                self.deepgram_api_key,
            ]

            if config:
                voice = config.get("voice")
                if voice:
                    cmd.extend(["--voice", str(voice)])

                think_provider = config.get("thinkProvider")
                if think_provider in ("open_ai", "anthropic", "groq"):
                    cmd.extend(["--think-provider", think_provider])

                think_model = config.get("thinkModel")
                if think_model:
                    cmd.extend(["--think-model", str(think_model)])

                prompt = config.get("prompt")
                if prompt:
                    cmd.extend(["--prompt", str(prompt)])

                greeting = config.get("greeting")
                if greeting:
                    cmd.extend(["--greeting", str(greeting)])

                language = config.get("language")
                if language:
                    cmd.extend(["--language", str(language)])

            logger.info(f"🚀 Launching Deepgram Agent for participant {participant_id}")
            logger.debug(f"Command: {' '.join(cmd)}")

            if self.verbose:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    bufsize=1,
                )
            else:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    universal_newlines=True,
                )

            self.active_instances[participant_id] = process
            self.instance_stage_arns[participant_id] = stage_arn
            logger.info(f"✅ Deepgram Agent launched for participant {participant_id} (PID: {process.pid})")

            asyncio.create_task(self.monitor_instance(participant_id, process))
            return True, "SUCCESS"

        except Exception as e:
            logger.error(f"❌ Failed to launch Deepgram Agent for participant {participant_id}: {e}")
            return False, "LAUNCH_FAILED"

    async def monitor_instance(self, participant_id: str, process: subprocess.Popen):
        try:
            if self.verbose and process.stdout:
                logger.info(f"📺 Starting output streaming for participant {participant_id}")
                stream_task = asyncio.create_task(self.stream_output(participant_id, process))

                async def wait_for_process():
                    return await asyncio.get_event_loop().run_in_executor(None, process.wait)

                wait_task = asyncio.create_task(wait_for_process())
                done, pending = await asyncio.wait([stream_task, wait_task], return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                return_code = wait_task.result() if wait_task in done else await asyncio.get_event_loop().run_in_executor(None, process.wait)
            else:
                return_code = await asyncio.get_event_loop().run_in_executor(None, process.wait)

            logger.info(f"🏁 Deepgram Agent for participant {participant_id} exited with code {return_code}")
        except Exception as e:
            logger.error(f"❌ Error monitoring instance for participant {participant_id}: {e}")
        finally:
            self.active_instances.pop(participant_id, None)
            self.instance_stage_arns.pop(participant_id, None)
            logger.info(f"🧹 Cleaned up instance for participant {participant_id}")

    async def stream_output(self, participant_id: str, process: subprocess.Popen):
        try:
            stage_arn = self.instance_stage_arns.get(participant_id, "unknown-stage")
            stage_id = stage_arn.split("/")[-1] if "/" in stage_arn else stage_arn

            while True:
                line = await asyncio.get_event_loop().run_in_executor(None, process.stdout.readline)
                if not line:
                    break
                line = line.rstrip("\n\r")
                if line:
                    print(f"[{stage_id}::{participant_id}] {line}")
        except Exception as e:
            logger.error(f"❌ Error streaming output for participant {participant_id}: {e}")

    async def send_error_response(self, error_code: str, stage_arn: str, participant_id: str, message: str = ""):
        try:
            if self.websocket:
                stage_id = stage_arn.split("/")[-1] if "/" in stage_arn else stage_arn
                error_response = {
                    "Action": "SEND_MESSAGE",
                    "Content": json.dumps(
                        {
                            "error": error_code,
                            "stageId": stage_id,
                            "participantId": participant_id,
                            "message": message,
                        }
                    ),
                }
                await self.websocket.send(json.dumps(error_response))
                logger.info(f"📤 Sent error response: {error_code} for {stage_id}::{participant_id}")
        except Exception as e:
            logger.error(f"❌ Failed to send error response: {e}")

    async def handle_message(self, message: str):
        try:
            chat_message = json.loads(message)
            logger.info(f"📨 Received chat message: {json.dumps(chat_message, indent=2)}")

            if chat_message.get("Type") != "MESSAGE" or "Content" not in chat_message:
                logger.info("ℹ️  Ignoring non-MESSAGE or message without Content")
                return

            sender_user_id = chat_message.get("Sender", {}).get("UserId")
            if sender_user_id == self.manager_user_id:
                logger.debug("🔄 Skipping own message")
                return

            try:
                data = json.loads(chat_message["Content"])
                logger.info(f"📦 Parsed payload: {json.dumps(data, indent=2)}")
            except json.JSONDecodeError:
                logger.error("❌ Content field is not valid JSON")
                return

            if "action" not in data or "stageArn" not in data or "participantId" not in data:
                logger.error("❌ Missing required fields: action, stageArn, participantId")
                return

            action = data["action"]
            stage_arn = data["stageArn"]
            participant_id = data["participantId"]

            if action != "LAUNCH_ASSISTANT":
                logger.info(f"ℹ️  Ignoring action '{action}' (expected 'LAUNCH_ASSISTANT')")
                return

            if not stage_arn or not participant_id:
                logger.error("❌ Empty stageArn or participantId")
                return

            config = {
                "voice": data.get("voice", "aura-2-asteria-en"),
                "thinkProvider": data.get("thinkProvider", "open_ai"),
                "thinkModel": data.get("thinkModel", "gpt-4o-mini"),
                "prompt": data.get("prompt"),
                "greeting": data.get("greeting"),
                "language": data.get("language"),
            }

            success, error_code = await self.launch_deepgram_agent_instance(stage_arn, participant_id, config)

            if success:
                logger.info(f"🎉 Launched Deepgram Agent for {participant_id}")
                logger.info(f"📊 Active instances: {len(self.active_instances)}/{self.max_instances}")
            else:
                logger.error(f"❌ Failed to launch for {participant_id}: {error_code}")
                await self.send_error_response(
                    error_code,
                    stage_arn,
                    participant_id,
                    f"Failed to launch Deepgram Agent for participant {participant_id}",
                )

        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON message: {e}")
        except Exception as e:
            logger.error(f"❌ Error handling message: {e}")

    async def connect_websocket(self):
        try:
            self.chat_token = await self.generate_chat_token()
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
        logger.info("🧹 Cleaning up active instances...")
        for participant_id, process in list(self.active_instances.items()):
            try:
                logger.info(f"🛑 Terminating instance for participant {participant_id}")
                process.terminate()
                try:
                    await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, process.wait),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"⚠️  Force killing instance for participant {participant_id}")
                    process.kill()
            except Exception as e:
                logger.error(f"❌ Error terminating instance for participant {participant_id}: {e}")

        self.active_instances.clear()
        self.instance_stage_arns.clear()
        logger.info("✅ Cleanup completed")

    async def run(self):
        try:
            await self.connect_websocket()
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down...")
        except Exception as e:
            logger.error(f"❌ Error in main loop: {e}")
        finally:
            await self.cleanup()


def parse_args():
    parser = argparse.ArgumentParser(
        description="IVS Stage Deepgram Agent Manager — spawns Deepgram Voice Agent instances via IVS Chat",
    )
    parser.add_argument("--chat-room-arn", required=True, help="IVS Chat room ARN")
    parser.add_argument("--ws-endpoint", required=True, help="WebSocket endpoint URL")
    parser.add_argument(
        "--deepgram-api-key",
        default=None,
        help="Deepgram API key (or set DEEPGRAM_API_KEY env var)",
    )
    parser.add_argument("--max-instances", type=int, default=5, help="Maximum concurrent agent instances (default: 5)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--verbose", action="store_true", help="Stream output from spawned agent instances")
    return parser.parse_args()


async def main():
    args = parse_args()

    deepgram_api_key = args.deepgram_api_key or os.getenv("DEEPGRAM_API_KEY")
    if not deepgram_api_key:
        logger.error("❌ Deepgram API key required. Use --deepgram-api-key or set DEEPGRAM_API_KEY env var")
        return

    logger.info("🎬 Starting IVS Stage Deepgram Agent Manager")
    logger.info(f"📺 Chat Room ARN: {args.chat_room_arn}")
    logger.info(f"🔌 WebSocket Endpoint: {args.ws_endpoint}")
    logger.info(f"🔢 Max Instances: {args.max_instances}")
    logger.info(f"🌍 Region: {args.region}")
    logger.info(f"📢 Verbose Output: {'enabled' if args.verbose else 'disabled'}")

    manager = IVSStageDeepgramAgentManager(
        chat_room_arn=args.chat_room_arn,
        ws_endpoint=args.ws_endpoint,
        deepgram_api_key=deepgram_api_key,
        max_instances=args.max_instances,
        region=args.region,
        verbose=args.verbose,
    )
    logger.info(f"👤 Manager User ID: {manager.manager_user_id}")

    await manager.run()


if __name__ == "__main__":
    asyncio.run(main())
