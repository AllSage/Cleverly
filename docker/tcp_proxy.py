"""Small TCP forwarder used by docker/offline.yml.

The app container stays on an internal Docker network with no internet egress.
This no-data sidecar publishes a localhost port and forwards only to the app.
"""

from __future__ import annotations

import asyncio
import sys


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    try:
        target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
    except Exception:
        client_writer.close()
        await client_writer.wait_closed()
        return

    await asyncio.gather(
        pipe(client_reader, target_writer),
        pipe(target_reader, client_writer),
    )


async def main() -> None:
    listen_host, listen_port, target_host, target_port = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
    server = await asyncio.start_server(
        lambda reader, writer: handle(reader, writer, target_host, target_port),
        listen_host,
        listen_port,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit("usage: tcp_proxy.py LISTEN_HOST LISTEN_PORT TARGET_HOST TARGET_PORT")
    asyncio.run(main())
