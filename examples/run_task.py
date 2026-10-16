import argparse
import asyncio
from getpass import getpass

from tabvio_agent import AgentSettings, BrowserAgentSession
from tabvio_agent.events import (
    AgentMessageDeltaEvent,
    InputRequiredEvent,
    RunCompletedEvent,
    RunFailedEvent,
    SensitiveInputRequiredEvent,
)


async def run_task(task: str) -> None:
    async with BrowserAgentSession(AgentSettings()) as session:
        await session.start(task)
        inline = False
        async for event in session.events():
            if isinstance(event, AgentMessageDeltaEvent):
                print(event.text, end="", flush=True)
                inline = not event.text.endswith("\n")
                continue
            if inline:
                print()
                inline = False
            print(event, flush=True)
            if isinstance(event, InputRequiredEvent):
                answer = await asyncio.to_thread(input, f"{event.question}\n> ")
                await session.answer(answer)
            elif isinstance(event, SensitiveInputRequiredEvent):
                code = await asyncio.to_thread(getpass, f"{event.prompt} (empty to decline): ")
                if code:
                    await session.submit_sensitive_input(event.request_id, code)
                else:
                    await session.decline_sensitive_input(event.request_id)
            elif isinstance(event, RunCompletedEvent):
                print(event.output)
                return
            elif isinstance(event, RunFailedEvent):
                raise SystemExit(event.error)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a browser task and stream its events.")
    parser.add_argument("task", nargs="*", help="The task for the browser agent.")
    args = parser.parse_args()
    if not args.task:
        parser.print_help()
        return
    asyncio.run(run_task(" ".join(args.task)))


if __name__ == "__main__":
    main()
