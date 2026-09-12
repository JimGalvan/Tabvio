import asyncio

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
