###############################################################################
## Copyright 2025-2026 Lawrence Livermore National Security, LLC.
## See the top-level LICENSE file for details.
##
## SPDX-License-Identifier: Apache-2.0
###############################################################################

import sys
from fastapi import WebSocket
from loguru import logger
from typing import Optional


# Define the callback function - will send message to the websocket if it is provided
async def handle_callback_log(message):
    record = message.record
    websocket = record["extra"].get("websocket", None)
    smiles = record["extra"].get("smiles", None)
    source = record["extra"].get("source", None)
    kwargs = {}
    if smiles:
        kwargs["smiles"] = smiles
    if websocket:
        # Timestamp is already included in the GUI window
        # timestamp = record["time"].isoformat(" ", timespec='seconds')
        msg = record["message"]
        level = record["level"].name
        if not source:
            LEVELS = {
                "DEBUG": "Debug",
                "VERBOSE": "Verbose",
                "INFO": "Info",
                "WARN": "Warning",
                "WARNING": "Warning",
                "ERROR": "Error",
                "EXCEPTION": "Exception",
            }
            level_str = LEVELS.get(level, level)
            source = f"Logger ({level_str})"
        try:
            await websocket.send_json(
                {
                    "type": "response",
                    "message": {"source": source, "message": msg, **kwargs},
                }
            )
        except (RuntimeError, Exception):
            # WebSocket is closed - silently ignore
            pass


logger.add(handle_callback_log, filter=lambda record: record["level"].name == "INFO")
logger.add(handle_callback_log, filter=lambda record: record["level"].name == "Info")
logger.add(handle_callback_log, filter=lambda record: record["level"].name == "Warning")
logger.add(handle_callback_log, filter=lambda record: record["level"].name == "Debug")
logger.add(handle_callback_log, filter=lambda record: record["level"].name == "Error")
logger.add(
    handle_callback_log, filter=lambda record: record["level"].name == "Exception"
)


# The Callback logger can hold a websocket that will allow the log message to be
# copied to the websocket as well as the logger
class CallbackLogger:
    def __init__(self, websocket: WebSocket, source: Optional[str] = None):
        self.websocket = websocket
        self.logger = logger.bind()
        self.source = source

    def _apply_msg_source(self, **kwargs):
        if self.source and (not kwargs or "source" not in kwargs):
            kwargs["source"] = self.source
        return kwargs

    async def _send(self, level: str, message: str, **kwargs):
        kwargs = self._apply_msg_source(**kwargs)
        log_kwargs = {k: v for k, v in kwargs.items() if k != "source"}
        if log_kwargs:
            logger.bind(**log_kwargs).log(level, message)
        else:
            logger.log(level, message)

        if self.websocket is None:
            return

        payload: dict[str, object] = {
            "type": "response",
            "message": {
                "source": kwargs.get("source", f"Logger ({level.title()})"),
                "message": message,
            },
        }
        if "smiles" in kwargs:
            payload["message"]["smiles"] = kwargs["smiles"]

        # Try to send, but silently fail if WebSocket is closed
        try:
            await self.websocket.send_json(payload)
        except (RuntimeError, Exception) as e:
            # WebSocket is closed or disconnected - log to console but don't raise
            logger.debug(
                f"Could not send message to WebSocket (likely closed): {str(e)}"
            )
            # Unbind the websocket so we don't keep trying to send to it
            self.websocket = None

    async def info(self, message, **kwargs):
        await self._send("INFO", message, **kwargs)

    async def warning(self, message, **kwargs):
        await self._send("WARNING", message, **kwargs)

    async def debug(self, message, **kwargs):
        await self._send("DEBUG", message, **kwargs)

    async def error(self, message, **kwargs):
        await self._send("ERROR", message, **kwargs)

    async def exception(self, message, **kwargs):
        await self._send("ERROR", message, **kwargs)

    def unbind(self):
        self.websocket = None
        self.logger = logger.bind()
