###############################################################################
## Copyright 2025-2026 Lawrence Livermore National Security, LLC.
## See the top-level LICENSE file for details.
##
## SPDX-License-Identifier: Apache-2.0
###############################################################################

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal


ToolKind = Literal["mcp", "builtin"]
ToolExecutionScope = Literal["backend", "local"]


def doc_summary(func: Callable[..., Any]) -> str:
    doc = inspect.getdoc(func)
    if not doc:
        return f"Run the backend function `{getattr(func, '__name__', 'tool')}`."
    return doc.splitlines()[0].strip()


@dataclass(frozen=True)
class MCPToolDefinition:
    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "MCPToolDefinition":
        return cls(
            name=str(payload["name"]),
            description=payload.get("description"),
            input_schema=payload.get("inputSchema"),
        )

    def json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass(frozen=True)
class ToolServerConfig:
    url: str
    scope: ToolExecutionScope
    id: str | None = None
    name: str | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ToolServerConfig":
        scope = payload.get("scope") or "backend"
        return cls(
            id=payload.get("id"),
            url=str(payload["url"]),
            name=payload.get("name"),
            scope="local" if scope == "local" else "backend",
        )

    def json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "name": self.name,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class BuiltinToolDefinition:
    identifier: str
    function: Callable[..., Any]
    label: str
    description: str

    def to_descriptor(self) -> "ToolDescriptor":
        return ToolDescriptor(
            kind="builtin",
            identifier=self.identifier,
            server=self.label,
            names=[self.function.__name__],
            description=self.description,
            execution_scope="backend",
            callable_tool=self.function,
        )

    def to_client_tool(self) -> dict[str, Any]:
        return self.to_descriptor().json()


def resolve_builtin_tools(
    identifiers: Iterable[str] | None,
    definitions: Iterable[BuiltinToolDefinition] | None = None,
) -> list[Callable[..., Any]]:
    return [
        tool.callable_tool
        for tool in resolve_builtin_tool_descriptors(identifiers, definitions)
        if tool.callable_tool is not None
    ]


def resolve_builtin_tool_descriptors(
    identifiers: Iterable[str] | None,
    definitions: Iterable[BuiltinToolDefinition] | None = None,
) -> list["ToolDescriptor"]:
    tool_definitions = list(definitions or [])
    if identifiers is None:
        return [tool.to_descriptor() for tool in tool_definitions]

    tool_map = {tool.identifier: tool for tool in tool_definitions}
    resolved_tools: list[ToolDescriptor] = []
    for identifier in identifiers:
        tool = tool_map.get(identifier)
        if tool is not None:
            resolved_tools.append(tool.to_descriptor())
    return resolved_tools


@dataclass(frozen=True)
class ToolDescriptor:
    kind: ToolKind
    identifier: str
    server: str
    names: list[str] | None = None
    description: str | None = None
    execution_scope: ToolExecutionScope = "backend"
    tools: list[MCPToolDefinition] | None = None
    allowed_tool_names: list[str] | None = None
    callable_tool: Any | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ToolDescriptor":
        raw_tools = payload.get("tools") or []
        return cls(
            kind=payload.get("kind", "mcp"),
            identifier=str(payload.get("identifier") or payload.get("server") or ""),
            server=str(payload.get("server") or ""),
            names=list(payload.get("names") or []) or None,
            description=payload.get("description"),
            execution_scope=(
                "local" if payload.get("executionScope") == "local" else "backend"
            ),
            tools=[
                MCPToolDefinition.from_json(tool)
                for tool in raw_tools
                if isinstance(tool, dict) and tool.get("name")
            ]
            or None,
            allowed_tool_names=list(payload.get("allowedToolNames") or []) or None,
        )

    def json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "server": self.server,
            "names": self.names,
            "description": self.description,
            "executionScope": self.execution_scope,
            "tools": [tool.json() for tool in self.tools] if self.tools else None,
            "allowedToolNames": self.allowed_tool_names,
        }


@dataclass
class ToolRuntime:
    tools: list[ToolDescriptor] = field(default_factory=list)
    bearer_token: str | None = None

    @property
    def tool_names(self) -> list[str]:
        resolved_names: list[str] = []
        seen_names: set[str] = set()

        def add_name(name: str | None) -> None:
            if not name or name in seen_names:
                return
            seen_names.add(name)
            resolved_names.append(name)

        for tool in self.tools:
            if tool.names:
                for name in tool.names:
                    add_name(name)
                continue

            if tool.tools:
                for mcp_tool in tool.tools:
                    add_name(mcp_tool.name)
                continue

            callable_name = getattr(tool.callable_tool, "__name__", None)
            if isinstance(callable_name, str):
                add_name(callable_name)
                continue

            declared_name = getattr(tool.callable_tool, "name", None)
            if isinstance(declared_name, str):
                add_name(declared_name)

        return resolved_names

    def tool_summary(self) -> str:
        tool_names = self.tool_names
        if tool_names:
            return ", ".join(tool_names)
        return "none"

    @property
    def mcp_server_urls(self) -> list[str]:
        server_urls: list[str] = []
        for tool in self.tools:
            if (
                tool.kind == "mcp"
                and tool.execution_scope == "backend"
                and tool.server
                and tool.server not in server_urls
            ):
                server_urls.append(tool.server)
        return server_urls

    @property
    def direct_tools(self) -> list[Any]:
        return [
            tool.callable_tool for tool in self.tools if tool.callable_tool is not None
        ]

    @property
    def local_mcp_tools(self) -> dict[str, list[MCPToolDefinition]]:
        tool_map: dict[str, list[MCPToolDefinition]] = {}
        for tool in self.tools:
            if tool.execution_scope != "local" or not tool.tools:
                continue
            tool_map[tool.server] = list(tool.tools)
        return tool_map

    @property
    def mcp_server_allowed_tools(self) -> dict[str, list[str]]:
        tool_map: dict[str, list[str]] = {}
        for tool in self.tools:
            if (
                tool.kind != "mcp"
                or tool.execution_scope != "backend"
                or not tool.server
            ):
                continue

            allowed_tool_names = tool.allowed_tool_names
            if allowed_tool_names is None and tool.tools:
                allowed_tool_names = [mcp_tool.name for mcp_tool in tool.tools]
            if allowed_tool_names is None and tool.names:
                allowed_tool_names = list(tool.names)
            if allowed_tool_names is None:
                continue

            unique_names = list(
                dict.fromkeys(name for name in allowed_tool_names if name)
            )
            if unique_names:
                tool_map[tool.server] = unique_names
        return tool_map

    def task_kwargs(self) -> dict[str, Any]:
        kwargs = {
            "server_urls": self.mcp_server_urls,
            "mcp_server_allowed_tools": self.mcp_server_allowed_tools,
            "builtin_tools": self.direct_tools,
        }
        if self.bearer_token is not None:
            kwargs["bearer_token"] = self.bearer_token
        return kwargs
