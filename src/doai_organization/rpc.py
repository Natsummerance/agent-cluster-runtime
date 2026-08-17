"""Bidirectional newline-delimited JSON-RPC transport for the stdio plane."""

from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
import uuid
from typing import Any, TextIO

from doai_protocol import RpcRequest

from .service import OrganizationService, RpcFault


class StdioRpcPeer:
    def __init__(self, reader: TextIO, writer: TextIO) -> None:
        self.reader = reader
        self.writer = writer
        self._write_lock = threading.Lock()
        self._requests: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._reader_thread = threading.Thread(target=self._read_loop, name="doai-rpc-reader", daemon=True)

    def start(self) -> None:
        self._reader_thread.start()

    async def call(
        self,
        method: str,
        params: dict[str, Any],
        mutation: dict[str, Any] | None = None,
    ) -> Any:
        return await asyncio.to_thread(self._call_blocking, method, params, mutation)

    async def serve(self, service: OrganizationService) -> None:
        self.start()
        tasks: set[asyncio.Task[None]] = set()
        while True:
            message = await asyncio.to_thread(self._requests.get)
            if message is None:
                break
            task = asyncio.create_task(self._handle_request(service, message))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _call_blocking(
        self,
        method: str,
        params: dict[str, Any],
        mutation: dict[str, Any] | None,
    ) -> Any:
        request_id = uuid.uuid4().hex
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        self._write({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
            **({} if mutation is None else {"mutation": mutation}),
        })
        response = response_queue.get()
        if "error" in response:
            error = response["error"]
            raise RpcFault(
                str(error.get("code", "HOST_RPC_FAILED")),
                str(error.get("message", "Host RPC failed")),
                bool(error.get("retryable", False)),
                error.get("details"),
            )
        return response.get("result")

    async def _handle_request(self, service: OrganizationService, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        try:
            request = RpcRequest.model_validate(message)
            result = await service.dispatch(request)
            self._write({"jsonrpc": "2.0", "id": request_id, "result": result})
        except RpcFault as fault:
            self._write({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": fault.code,
                    "message": fault.message,
                    "retryable": fault.retryable,
                    **({} if fault.details is None else {"details": fault.details}),
                },
            })
        except Exception as error:
            self._write({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": str(error),
                    "retryable": False,
                },
            })

    def _read_loop(self) -> None:
        try:
            for line in self.reader:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._write({
                        "jsonrpc": "2.0", "id": None,
                        "error": {"code": "PARSE_ERROR", "message": "invalid JSON", "retryable": False},
                    })
                    continue
                if "method" in message:
                    self._requests.put(message)
                    continue
                request_id = str(message.get("id"))
                with self._pending_lock:
                    target = self._pending.pop(request_id, None)
                if target is not None:
                    target.put(message)
        finally:
            with self._pending_lock:
                pending = list(self._pending.values())
                self._pending.clear()
            failure = {
                "error": {"code": "HOST_STREAM_CLOSED", "message": "Host closed the RPC stream", "retryable": True},
            }
            for target in pending:
                target.put(failure)
            self._requests.put(None)

    def _write(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            self.writer.write(encoded + "\n")
            self.writer.flush()


async def serve_stdio() -> None:
    peer = StdioRpcPeer(sys.stdin, sys.stdout)
    await peer.serve(OrganizationService(peer))
