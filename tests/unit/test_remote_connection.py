import asyncio
import json

import pytest

from tabvio.remote.connection import Connection


def test_nested_requests_do_not_block_the_connection():
    async def check():
        inbound = asyncio.Queue()
        outbound = asyncio.Queue()

        async def client_handler(method, params):
            return params["value"] * 2

        async def worker_handler(method, params):
            return await worker.call("double", params)

        client = Connection(outbound.put, inbound.get, client_handler)
        worker = Connection(inbound.put, outbound.get, worker_handler)
        listeners = [asyncio.create_task(client.listen()), asyncio.create_task(worker.listen())]
        try:
            assert await client.call("work", {"value": 21}, timeout=1) == 42
        finally:
            for listener in listeners:
                listener.cancel()
            await asyncio.gather(*listeners, return_exceptions=True)

    asyncio.run(check())


def test_large_concurrent_messages_stay_below_the_frame_limit():
    async def check():
        inbound = asyncio.Queue()
        outbound = asyncio.Queue()
        frames = []

        def sender(queue):
            async def send(message):
                frames.append(len(json.dumps(message).encode("utf-8")))
                await queue.put(message)
            return send

        async def handle(method, params):
            return params["value"]

        client = Connection(sender(outbound), inbound.get, handle)
        worker = Connection(sender(inbound), outbound.get, handle)
        listeners = [asyncio.create_task(client.listen()), asyncio.create_task(worker.listen())]
        values = ["screenshot" * 30000, "é🙂" * 20000, "small"]
        try:
            results = await asyncio.gather(*[
                client.call("echo", {"value": value}, timeout=5) for value in values
            ])
            assert results == values
            assert max(frames) < 32 * 1024
        finally:
            for listener in listeners:
                listener.cancel()
            await asyncio.gather(*listeners, return_exceptions=True)

    asyncio.run(check())


def test_oversized_received_message_is_rejected(monkeypatch):
    monkeypatch.setattr("tabvio.remote.connection.MAX_MESSAGE_BYTES", 4)

    async def check():
        queue = asyncio.Queue()
        await queue.put({"chunk": "MTIzNDU=", "final": True})
        connection = Connection(queue.put, queue.get, None)
        with pytest.raises(ConnectionError, match="too large"):
            await connection.receive()

    asyncio.run(check())


def test_disconnect_releases_pending_requests():
    async def check():
        inbound = asyncio.Queue()

        async def send(message):
            pass

        async def handle(method, params):
            pass

        connection = Connection(send, inbound.get, handle)
        listener = asyncio.create_task(connection.listen())
        request = asyncio.create_task(connection.call("work"))
        await asyncio.sleep(0)
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        with pytest.raises(ConnectionError):
            await request
        assert not connection._pending

    asyncio.run(check())
