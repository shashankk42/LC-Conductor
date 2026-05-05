###############################################################################
## Copyright 2025-2026 Lawrence Livermore National Security, LLC.
## See the top-level LICENSE file for details.
##
## SPDX-License-Identifier: Apache-2.0
###############################################################################

from typing import Any, Literal, Optional, Tuple
from fastapi import WebSocket
import asyncio
import os
import traceback
from loguru import logger
from lc_conductor.callback_logger import CallbackLogger
from concurrent.futures import ProcessPoolExecutor
from charge.experiments.experiment import Experiment
from charge.clients.agent_factory import AgentFactory
from charge.clients.agentframework import AgentFrameworkBackend

from functools import partial
from lc_conductor.tool_registration import (
    list_server_urls,
    list_server_tools,
    extract_bearer_token_from_headers,
)
from lc_conductor.backend_helper_function import RunSettings
from lc_conductor.local_mcp_proxy import (
    attach_local_mcp_tools,
    cancel_pending_local_mcp_requests,
    list_local_mcp_tools,
    LocalMcpProxyDisconnected,
)
from lc_conductor.tooling import (
    BuiltinToolDefinition,
    MCPToolDefinition,
    ToolDescriptor,
    ToolRuntime,
    ToolServerConfig,
    resolve_builtin_tool_descriptors,
)

# Mapping from backend name to human-readable labels. Mirrored from the frontend
BACKEND_LABELS = {
    "openai": "OpenAI",
    "livai": "LivAI",
    "llamame": "LLamaMe",
    "alcf": "ALCF Sophia",
    "gemini": "Google Gemini",
    "ollama": "Ollama",
    "vllm": "vLLM",
    "huggingface": "HuggingFace Local",
    "custom": "Custom URL",
}


class TaskManager:
    """Manages background tasks and processes state for a websocket connection."""

    def __init__(self, websocket: WebSocket, max_workers: int = 4):
        self.websocket = websocket
        self.current_task: Optional[asyncio.Task] = None
        self.clogger = CallbackLogger(websocket, source="backend_manager")
        self.max_workers = max_workers
        self.executor = ProcessPoolExecutor(max_workers=max_workers)
        self.configured_tool_servers: list[ToolServerConfig] = []
        self.discovered_local_mcp_tools: dict[str, list[MCPToolDefinition]] = {}
        self.selected_tool_runtime: Optional[ToolRuntime] = None

    def _attach_done_callback(self, task: asyncio.Task) -> None:
        """Attach a done-callback to a background task so exceptions are observed.

        The callback forwards useful error metadata to the websocket and logs
        the exception type/module so class-identity mismatches (multiple
        installations of `charge`) can be diagnosed.
        """
        if task is None:
            return
        task.add_done_callback(lambda t: asyncio.create_task(self._handle_task_done(t)))

    async def _handle_task_done(self, task: asyncio.Task) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            logger.info("Background task was cancelled")
            return

        if exc is None:
            return

        # Log the exception details
        tb = "".join(traceback.format_exception(exc))
        msg = f"Background task failed with exception: {type(exc).__name__}: {tb}"
        logger.error(msg)

        # Try to send error to WebSocket, but handle disconnection gracefully
        try:
            await self.websocket.send_json(
                {
                    "type": "response",
                    "message": {
                        "source": "system",
                        "message": msg,
                    },
                }
            )
        except (RuntimeError, Exception) as send_error:
            logger.debug(
                f"Could not send error to WebSocket (likely closed): {send_error}"
            )

        # Send a stopped message with error details to the websocket so the UI can react
        try:
            await self.websocket.send_json({"type": "complete"})
        except (RuntimeError, Exception) as send_error:
            logger.debug(
                f"Could not send complete to WebSocket (likely closed): {send_error}"
            )

    async def run_task(self, coro) -> None:
        await self.cancel_current_task()
        try:
            self.current_task = asyncio.create_task(coro)
            self._attach_done_callback(self.current_task)
            await self.current_task  # Await it to catch exceptions properly
        except asyncio.CancelledError:
            logger.info("Task was cancelled")
            raise
        except Exception as e:
            logger.error(f"Task failed: {e}")
            await self.websocket.send_json({"type": "complete"})
            # Optionally re-raise or handle as needed

    async def cancel_current_task(self) -> None:
        if self.current_task and not self.current_task.done():
            logger.info("Cancelling current task...")
            self.current_task.cancel()
            try:
                await self.current_task
            except asyncio.CancelledError:
                logger.info("Current task cancelled successfully.")
        await self.restart_executor()

    async def restart_executor(self) -> None:
        """Shutdown and recreate the process pool executor."""
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.executor = ProcessPoolExecutor(max_workers=self.max_workers)

    async def close(self) -> None:
        await self.cancel_current_task()
        cancel_pending_local_mcp_requests(self.websocket)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.clogger.unbind()


class ActionManager:
    """Handles action state for a websocket connection."""

    def __init__(
        self,
        task_manager: TaskManager,
        experiment: Experiment,
        args,
        username: str,
        builtin_tool_definitions: Optional[list[BuiltinToolDefinition]] = None,
    ):
        self.task_manager = task_manager
        self.experiment = experiment
        self.args = args
        self.username = username
        self.run_settings: RunSettings = RunSettings()
        self.reasoning_effort: Literal["low", "medium", "high"] = "medium"
        self.websocket = task_manager.websocket
        self.builtin_tool_definitions = builtin_tool_definitions or []
        if not self.task_manager.configured_tool_servers:
            # Sync configured_tool_servers with registered servers from SERVERS global
            try:
                server_urls = list_server_urls(bearer_token=self._get_wormhole_token())
                self.task_manager.configured_tool_servers = [
                    ToolServerConfig(url=server_url, scope="backend")
                    for server_url in server_urls
                ]
            except Exception as e:
                logger.warning(
                    f"Failed to load cached MCP servers during initialization: {e}. "
                    "Starting with no configured tool servers."
                )
                self.task_manager.configured_tool_servers = []

    def _get_wormhole_token(self) -> Optional[str]:
        """Extract wormhole community subtoken from websocket headers."""
        return extract_bearer_token_from_headers(self.websocket)

    def setup_run_settings(self, data: dict[str, Any]):
        if "runSettings" in data:
            self.run_settings = RunSettings(**data["runSettings"])

    async def handle_save_state(self, data, *args, **kwargs) -> None:
        """Handle save state action."""
        logger.trace("Save state action received")
        self.setup_run_settings(data)

        experiment_context = await self.experiment.save_state()
        await self.websocket.send_json(
            {"type": "save-context-response", "experimentContext": experiment_context}
        )

    async def handle_load_state(self, data, *args, **kwargs) -> None:
        """Handle load state action."""
        logger.trace("Load state action received")
        experiment_context = data.get("experimentContext")
        if not experiment_context:
            logger.debug("No experiment context provided for loading state")
            return
        await self.experiment.load_state(experiment_context)

    async def _send_processing_message(
        self, message: str, source: str | None = None, **kwargs
    ) -> None:
        """Send a processing message to the client."""
        await self.websocket.send_json(
            {
                "type": "response",
                "message": {
                    "source": source or "System",
                    "message": message,
                },
                **kwargs,
            }
        )

    def _configured_local_tool_servers(self) -> list[str]:
        return [
            server.url
            for server in self.task_manager.configured_tool_servers
            if server.scope == "local"
        ]

    def _configured_backend_tool_servers(self) -> list[str]:
        backend_servers = [
            server.url
            for server in self.task_manager.configured_tool_servers
            if server.scope == "backend"
        ]
        return backend_servers or list_server_urls()

    def _build_tool_runtime(
        self,
        descriptors: list[ToolDescriptor] | None = None,
    ) -> ToolRuntime:
        # Extract wormhole token from websocket headers
        wormhole_token = self._get_wormhole_token()

        if descriptors is None:
            runtime = ToolRuntime(
                bearer_token=wormhole_token,
                tools=[
                    *[
                        ToolDescriptor(
                            kind="mcp",
                            identifier=server_url,
                            server=server_url,
                            execution_scope="backend",
                        )
                        for server_url in self._configured_backend_tool_servers()
                    ],
                    *resolve_builtin_tool_descriptors(
                        None,
                        self.builtin_tool_definitions,
                    ),
                    *[
                        ToolDescriptor(
                            kind="mcp",
                            identifier=server_url,
                            server=server_url,
                            names=[tool.name for tool in local_tools],
                            description="Local MCP server proxied through the browser session.",
                            execution_scope="local",
                            tools=list(local_tools),
                        )
                        for server_url, local_tools in self.task_manager.discovered_local_mcp_tools.items()
                    ],
                ],
            )
            return attach_local_mcp_tools(self.websocket, runtime)

        selected_builtin_tool_ids: list[str] = []
        selected_descriptors: list[ToolDescriptor] = []

        for descriptor in descriptors:
            if descriptor.kind == "builtin":
                selected_builtin_tool_ids.append(descriptor.identifier)
                continue

            selected_descriptors.append(descriptor)

        runtime = ToolRuntime(
            bearer_token=wormhole_token,
            tools=[
                *selected_descriptors,
                *resolve_builtin_tool_descriptors(
                    selected_builtin_tool_ids,
                    self.builtin_tool_definitions,
                ),
            ],
        )
        return attach_local_mcp_tools(self.websocket, runtime)

    def selected_tool_runtime(self) -> ToolRuntime:
        if self.task_manager.selected_tool_runtime is not None:
            return self.task_manager.selected_tool_runtime
        return self._build_tool_runtime()

    async def handle_list_tools(self, *args, **kwargs) -> None:
        tools: list[ToolDescriptor] = []
        server_list = self._configured_backend_tool_servers()
        for server in server_list:
            try:
                # Collect the wormhole community subtoken from websocket headers
                # then pass it as the bearer token
                wormhole_token = self._get_wormhole_token()
                tool_list = await list_server_tools(
                    [server], bearer_token=wormhole_token
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to enumerate backend MCP tools from {server}: {exc}"
                )
                continue

            tool_names = [name for name, _ in tool_list]
            tools.append(
                ToolDescriptor(
                    kind="mcp",
                    identifier=server,
                    server=server,
                    names=tool_names,
                    tools=[
                        MCPToolDefinition(
                            name=name,
                            description=description,
                        )
                        for name, description in tool_list
                    ]
                    or None,
                    execution_scope="backend",
                )
            )

        try:
            local_tool_map = await list_local_mcp_tools(
                self.websocket,
                self._configured_local_tool_servers(),
            )
        except LocalMcpProxyDisconnected:
            logger.info(
                "Skipping local MCP tool enumeration because the websocket disconnected"
            )
            return
        except Exception as exc:
            logger.warning(f"Failed to enumerate local MCP tools: {exc}")
            local_tool_map = {}

        self.task_manager.discovered_local_mcp_tools = local_tool_map
        for server, local_tools in local_tool_map.items():
            tools.append(
                ToolDescriptor(
                    kind="mcp",
                    identifier=server,
                    server=server,
                    names=[tool.name for tool in local_tools],
                    description="Local MCP server proxied through the browser session.",
                    execution_scope="local",
                    tools=local_tools,
                )
            )

        tools.extend(
            tool_definition.to_descriptor()
            for tool_definition in self.builtin_tool_definitions
        )

        try:
            await self.websocket.send_json(
                {
                    "type": "available-tools-response",
                    "tools": [tool.json() for tool in tools] if tools else [],
                }
            )
        except RuntimeError:
            logger.info(
                "Skipping available tools response because the websocket disconnected"
            )

    async def report_orchestrator_config(self) -> Tuple[str, str, str]:
        agent_backend = AgentFactory.default_backend()
        # Access specific fields
        base_url = agent_backend.base_url
        model = agent_backend.model
        logger.trace(
            f"Reporting orchestrator config: backend={agent_backend.backend}, model={model}, base_url={base_url}"
        )

        # Resync configured_tool_servers with registered servers from SERVERS global
        # This ensures frontend sees all registered servers, not just the initial list
        try:
            backend_server_urls = list_server_urls(
                bearer_token=self._get_wormhole_token()
            )
            self.task_manager.configured_tool_servers = [
                ToolServerConfig(url=url, scope="backend")
                for url in backend_server_urls
            ]
        except Exception as e:
            logger.warning(
                f"Failed to resync MCP servers: {e}. Keeping existing configuration."
            )
            # Keep existing configured_tool_servers if resync fails

        if agent_backend.backend in ["livai", "livchat", "llamame", "alcf"]:
            useCustomUrl = True
        else:
            useCustomUrl = False
        await self.websocket.send_json(
            {
                "type": "server-update-orchestrator-settings",
                "orchestratorSettings": {
                    "backend": agent_backend.backend,
                    "backendLabel": BACKEND_LABELS.get(
                        agent_backend.backend, agent_backend.backend
                    ),
                    "useCustomUrl": useCustomUrl,
                    "customUrl": base_url if base_url else "",
                    "model": model,
                    "reasoningEffort": getattr(
                        agent_backend, "reasoning_effort", self.reasoning_effort
                    ),
                    "useCustomModel": False,
                    "apiKey": "",
                    "toolServers": [
                        tool_server.json()
                        for tool_server in self.task_manager.configured_tool_servers
                    ],
                },
            }
        )
        return agent_backend.backend, model, base_url

    async def handle_orchestrator_settings_update(self, data: dict) -> None:
        tool_server_payloads = [
            server
            for server in data.get("toolServers", [])
            if isinstance(server, dict) and server.get("url")
        ]
        self.task_manager.configured_tool_servers = [
            ToolServerConfig.from_json(server) for server in tool_server_payloads
        ]
        self.task_manager.discovered_local_mcp_tools = {}
        self.task_manager.selected_tool_runtime = None

        backend = data["backend"]
        model = data["model"]
        use_custom_url = bool(data.get("useCustomUrl"))
        base_url = data["customUrl"] if use_custom_url and data["customUrl"] else None

        # Treat frontend defaults as "not set" - allow env vars to override
        if base_url in ["http://localhost:8000/v1", "http://localhost:8000"]:
            logger.info(
                f"Received default URL {base_url} from frontend, will check environment variables"
            )
            base_url = None

        api_key = data["apiKey"] if data["apiKey"] else None
        reasoning_effort = data.get("reasoningEffort") or "medium"
        self.reasoning_effort = reasoning_effort
        await self.handle_reset()

        # Default to server defaults
        if backend == os.getenv("FLASK_ORCHESTRATOR_BACKEND", None):
            if not api_key:
                api_key = os.getenv("FLASK_ORCHESTRATOR_API_KEY", None)
            if not base_url:
                base_url = os.getenv("FLASK_ORCHESTRATOR_URL", None)

        try:
            logger.info(
                f"Experiment is reset with model {model}, backend {backend}"
                f", and reasoning effort {reasoning_effort}."
            )
            AgentFactory.register_backend(
                "agentframework",
                AgentFrameworkBackend(
                    model=model,
                    backend=backend,
                    api_key=api_key,
                    base_url=base_url,
                    use_responses_api=True,
                    reasoning_effort=reasoning_effort,
                ),
            )
            # Set up an experiment class for current endpoint
            self.experiment = Experiment(task=None)

            # Report the new orchestrator config to the frontend
            await self.report_orchestrator_config()

            await self.websocket.send_json(
                {
                    "type": "response",
                    "message": {
                        "source": "System",
                        "message": f"Experiment is reset with model {model}, backend {backend},"
                        f" and reasoning effort {reasoning_effort}.",
                    },
                }
            )
        except ValueError as e:
            logger.error(
                f"Orchestrator Profile Error: Unable to restart experiment: {e}"
            )
            backend, model, base_url = await self.report_orchestrator_config()
            await self.websocket.send_json(
                {
                    "type": "response",
                    "message": {
                        "source": "System",
                        "message": f"Orchestrator Profile Error: Unable to restart experiment: {e}. Experiment is still using backend {backend} with model {model} at {base_url}",
                    },
                }
            )

    async def handle_reset(self, *args, **kwargs) -> None:
        """Handle reset action."""
        await self.task_manager.cancel_current_task()
        self.experiment.reset()
        self.retro_synth_context = None

    async def handle_stop(self, *args, **kwargs) -> None:
        """Handle stop action."""
        logger.info("Stop action received")
        if self.task_manager.current_task:
            if not self.task_manager.current_task.done():
                logger.info("Stopping current task as per user request.")
                await self.task_manager.cancel_current_task()

                # Send confirmation to frontend
                try:
                    await self.websocket.send_json({"type": "stopped"})
                    logger.info("Sent 'stopped' confirmation to frontend")
                except Exception as e:
                    logger.error(f"Failed to send stopped confirmation: {e}")
            else:
                logger.info(f"Task already done: {self.task_manager.current_task}")
                await self.websocket.send_json({"type": "stopped"})
        else:
            logger.info(
                f"No active task to stop. Task done: {self.task_manager.current_task.done() if self.task_manager.current_task else 'N/A'}"
            )
            try:
                await self.websocket.send_json({"type": "stopped"})
            except Exception as e:
                logger.error(f"Failed to send stopped confirmation: {e}")

    async def handle_select_tools_for_task(self, data: dict) -> None:
        """Handle select-tools-for-task action."""
        logger.info("Select tools for task")
        logger.info(f"Data: {data}")
        descriptors = [
            ToolDescriptor.from_json(server["tool_server"])
            for server in data.get("enabledTools", {}).get("selectedTools", [])
            if isinstance(server, dict) and isinstance(server.get("tool_server"), dict)
        ]
        self.task_manager.selected_tool_runtime = self._build_tool_runtime(descriptors)

    async def handle_get_username(self, _: dict) -> None:
        await self.websocket.send_json(
            {
                "type": "get-username-response",
                "username": self.username,
            }
        )
