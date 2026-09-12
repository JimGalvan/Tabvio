import argparse
import asyncio
import json
from uuid import uuid4

import boto3

from tabvio.remote.runtime import RemoteAgentRuntime


async def check(runtime_arn):
    runtime = RemoteAgentRuntime(runtime_arn, uuid4(), uuid4(), (), None)
    print(json.dumps({"run_id": str(runtime.thread_id)}), flush=True)
    events = []
    try:
        async with asyncio.timeout(300):
            task = (
                "Open https://example.com and read the page. Then use request_user_input "
                "to ask whether I want a one-sentence summary. Wait for my answer before summarizing."
            )
            async for event in runtime.stream(runtime.start_input(task)):
                if event.get("event_type"):
                    events.append(event["event_type"])
                    print(event["event_type"], flush=True)
            if "input.required" not in events:
                raise RuntimeError("The agent did not pause for input")
            frame = await runtime.browser.capture_screen_frame(45)
            if not frame:
                raise RuntimeError("The remote browser did not return a frame")
            print(f"Browser frame: {len(frame)} bytes", flush=True)
            await runtime.browser.user_scroll(300, 300, 100)
            print("Human browser control: passed", flush=True)
            async for event in runtime.stream(runtime.resume_input("Yes, summarize it in one sentence.")):
                if event.get("event_type"):
                    print(event["event_type"], flush=True)
            output = await runtime.final_output()
            if not output:
                raise RuntimeError("The resumed agent did not return a result")
            print(json.dumps({"result": output}), flush=True)
    finally:
        await runtime.close()
        client = boto3.client("bedrock-agentcore", region_name="us-west-2")
        active = []
        token = None
        while True:
            params = {"browserIdentifier": "aws.browser.v1"}
            if token:
                params["nextToken"] = token
            response = await asyncio.to_thread(client.list_browser_sessions, **params)
            active.extend(item["sessionId"] for item in response.get("items", []) if item["status"] != "TERMINATED")
            token = response.get("nextToken")
            if not token:
                break
        print(json.dumps({"remaining_browser_sessions": active}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime_arn")
    args = parser.parse_args()
    asyncio.run(check(args.runtime_arn))
