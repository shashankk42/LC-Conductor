################################################################################
## Copyright 2025-2026 Lawrence Livermore National Security, LLC.
## See the top-level LICENSE file for details.
##
## SPDX-License-Identifier: Apache-2.0
################################################################################

from dataclasses import dataclass, asdict
from fastapi import Request
import socket
from loguru import logger
import requests
from pydantic import BaseModel
import json
from typing import Any, Optional, Tuple, List, Dict
import time
import os
import re
import asyncio
from mcp.server.fastmcp import FastMCP

from charge.utils.mcp_workbench_utils import (
    list_mcp_tools_direct,
)

from charge.utils.system_utils import check_server_paths, check_url_exists


def extract_bearer_token_from_headers(headers_obj) -> Optional[str]:
    """
    Extract bearer token (x-subtoken) from request or websocket headers.

    This is a common helper to extract the wormhole community subtoken used for
    MCP server authentication. Works with FastAPI Request objects, WebSocket objects,
    or any object with a .headers attribute.

    Args:
        headers_obj: Object with .headers attribute (Request, WebSocket, etc.)
                     Can also be a dict-like headers object directly.

    Returns:
        Bearer token string if found, None otherwise
    """
    # Handle dict-like headers directly
    if isinstance(headers_obj, dict):
        token = headers_obj.get("x-subtoken")
        if token:
            logger.trace(f"Extracted bearer token from headers (length: {len(token)})")
            return token
        logger.trace("No bearer token (x-subtoken) found in headers")
        return None

    # Handle objects with .headers attribute (Request, WebSocket)
    if hasattr(headers_obj, "headers"):
        headers = headers_obj.headers
        # Check if it's a dict-like object or has .get method
        if hasattr(headers, "get"):
            token = headers.get("x-subtoken")
        elif "x-subtoken" in headers:
            token = headers["x-subtoken"]
        else:
            token = None

        if token:
            logger.trace(f"Extracted bearer token from headers (length: {len(token)})")
            return token
        logger.trace("No bearer token (x-subtoken) found in headers")
        return None

    logger.warning(f"Cannot extract headers from object of type: {type(headers_obj)}")
    return None


class CheckServersRequest(BaseModel):
    urls: list[str]


class ValidateMCPServerRequest(BaseModel):
    url: str
    name: Optional[str] = None


class DeleteMCPServerRequest(BaseModel):
    url: str


@dataclass
class ToolList:
    server: str
    names: Optional[list[str]] = None
    description: Optional[str] = None
    kind: str = "mcp"
    identifier: Optional[str] = None
    executionScope: str = "backend"
    tools: Optional[list[dict[str, Any]]] = None

    def json(self):
        return asdict(self)


class ToolServer(BaseModel):
    url: str
    name: str

    def __str__(self):
        return self.url

    def long_name(self):
        short_name = self.__str__()
        return f"[{self.name}] {short_name}"


class ToolServerDict(BaseModel):
    servers: dict[str, ToolServer]


SERVERS: ToolServerDict = ToolServerDict(servers={})


def get_client_info(request: Request):
    """Get client IP and hostname with fallbacks"""
    # Try to get real IP from X-Forwarded-For header
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        # Fallback to direct connection IP
        if request.client is not None:
            client_ip = request.client.host
        else:
            client_ip = "0.0.0.0"

    # Try to resolve hostname
    try:
        hostname = socket.gethostbyaddr(client_ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        hostname = client_ip  # Use IP if resolution fails

    return hostname


@dataclass
class RegistrationRequest:
    host: str
    port: int
    name: str  # Should this be path


def reload_server_list(filename: str):
    if filename:
        # Check if file exists
        if not os.path.exists(filename):
            logger.info(f"Server list file does not exist: {filename}")
            return

        # Check if file is readable
        if not os.access(filename, os.R_OK):
            logger.error(f"Server list file is not readable: {filename}")
            return

        # Check if file has non-zero size
        try:
            file_size = os.path.getsize(filename)
            if file_size == 0:
                logger.info(f"Server list file is empty (0 bytes): {filename}")
                return
        except OSError as e:
            logger.error(f"Error getting file size for {filename}: {e}")
            return

        try:
            with open(filename, "r") as f:
                data: ToolServerDict = ToolServerDict.model_validate_json(f.read())
                SERVERS.servers = data.servers
        except FileNotFoundError as e:
            logger.info(e)
            return
        except json.JSONDecodeError as e:
            logger.info(e)
            return
        except Exception as e:
            logger.info(e)
            return
    else:
        return


def register_url(
    filename: str,
    url: str,
    name: str = "",
):
    key = url
    new_server = ToolServer(url=url, name=name)

    old_server = SERVERS.servers.pop(key, None)
    if old_server:
        logger.info(
            f"Replacing server at {key} with new registration: {old_server.long_name()} -> {new_server.long_name()}"
        )

    SERVERS.servers[key] = new_server

    if filename:
        # Check if file exists
        file_exists = os.path.exists(filename)

        msg_base = f"registered MCP server {name} at {key}"
        # Check if file is writable (or parent directory is writable for new files)
        if file_exists:
            if not os.access(filename, os.W_OK):
                logger.error(f"Server list file is not writable: {filename}")
                return {
                    "status": f"{msg_base} (warning: could not save to disk - file not writable)"
                }
        else:
            # For new files, check if parent directory is writable
            parent_dir = os.path.dirname(filename) or "."
            if not os.access(parent_dir, os.W_OK):
                logger.error(
                    f"Cannot create server list file - parent directory not writable: {parent_dir}"
                )
                return {
                    "status": f"{msg_base} (warning: could not save to disk - directory not writable)"
                }

        try:
            with open(filename, "w") as f:
                f.write(SERVERS.model_dump_json(indent=4))
        except PermissionError as e:
            logger.error(
                f"Permission denied writing to server list file {filename}: {e}"
            )
            return {
                "status": f"{msg_base} (warning: could not save to disk - permission denied)"
            }
        except OSError as e:
            logger.error(f"OS error writing to server list file {filename}: {e}")
            return {"status": f"{msg_base} (warning: could not save to disk - {e})"}
        except Exception as e:
            logger.error(f"Error writing to server list file {filename}: {e}")
            return {"status": f"{msg_base} (warning: could not save to disk)"}

        return {"status": f"{msg_base}"}
    else:
        return {"status": f"ERROR: No file name provided"}


async def register_post(filename: str, request: Request, data: RegistrationRequest):
    hostname = data.host
    if not hostname:
        hostname = get_client_info(request)

    url = f"http://{hostname}"
    if data.port:
        url += f":{data.port}"
    if data.name:
        url += f"/{data.name}"
    return register_url(filename, url, data.name)


def register_tool_server(port, host, name, copilot_port, copilot_host):
    max_retries = 1
    for i in range(max_retries):
        try:
            try:
                url = f"https://{copilot_host}:{copilot_port}/register"
                response = requests.post(
                    url, json={"host": host, "port": port, "name": name}
                )
            except:
                url = f"http://{copilot_host}:{copilot_port}/register"
                response = requests.post(
                    url, json={"host": host, "port": port, "name": name}
                )
            logger.info(response.json())
            break
        except:
            if i == max_retries:
                logger.error("Could not connect to server for registration! Exiting")
                raise
            logger.info(
                "Could not connect to server for registration, retrying in 10 seconds"
            )
            time.sleep(10)
            continue


async def check_mcp_servers_endpoint(request: Request, data: CheckServersRequest):
    """
    Check connectivity status of multiple MCP server URLs.
    Returns status and tools for each URL.

    Uses existing workbench utilities for validation.
    """
    from lc_conductor.tool_registration import _check_mcp_connectivity

    # Extract bearer token from request headers
    bearer_token = extract_bearer_token_from_headers(request)

    results = {}

    for url in data.urls:
        try:
            tools = await _check_mcp_connectivity(
                url, timeout=5.0, bearer_token=bearer_token
            )
            results[url] = {"status": "connected", "tools": tools}
        except Exception as e:
            results[url] = {"status": "disconnected", "error": str(e)}

    return {"results": results}


async def _check_mcp_connectivity(
    url: str, timeout: float, bearer_token: Optional[str]
) -> List[Dict]:
    """
    Connect to an MCP server and retrieve its tools list using direct MCP client.

    Args:
        url: MCP server URL (should end with /mcp)
        timeout: Connection timeout in seconds
        bearer_token: Optional bearer token for authentication

    Returns:
        List of tools with name and description

    Raises:
        Exception: If connection fails or server is unreachable
    """
    # Ensure URL ends with /mcp
    mcp_url = url if url.endswith("/mcp") else f"{url.rstrip('/')}/mcp"

    # First do a quick check if the URL is reachable
    if not check_url_exists(mcp_url, bearer_token):
        raise Exception(f"Server at {mcp_url} is not reachable")

    try:
        tools_by_server = await list_mcp_tools_direct(
            urls=[mcp_url], paths=[], bearer_token=bearer_token
        )

        # Extract tools for this server
        if mcp_url not in tools_by_server:
            raise Exception(f"Failed to get tools from server {mcp_url}")

        server_tools = tools_by_server[mcp_url]

        # Check for errors
        if isinstance(server_tools, dict) and "error" in server_tools:
            raise Exception(server_tools["error"])

        # Convert to expected format
        tools = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
            }
            for tool in server_tools
        ]

        return tools

    except asyncio.TimeoutError:
        raise TimeoutError("Connection timeout")
    except Exception as e:
        raise Exception(f"Validation error: {str(e)}")


async def validate_and_register_mcp_server(
    filename: str,
    url: str,
    name: Optional[str] = None,
    bearer_token: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict:
    """
    Validate an MCP server URL by connecting to it and listing tools.
    If valid, register it.

    Args:
        filename: Path to server cache file
        url: MCP server URL
        name: Optional display name for the server
        timeout: Connection timeout in seconds

    Returns:
        Dict with status, tools (if successful), and error message (if failed)
    """
    # Ensure URL ends with /mcp
    mcp_url = url if url.endswith("/mcp") else f"{url.rstrip('/')}/mcp"

    # Validate connectivity using existing utilities
    try:
        tools = await _check_mcp_connectivity(mcp_url, timeout, bearer_token)

        # If validation successful, register the server
        if not name:
            name = mcp_url

        registration_result = register_url(filename, mcp_url, name)

        return {
            "status": "connected",
            "tools": tools,
            "url": mcp_url,
            "registration": registration_result,
        }

    except Exception as e:
        logger.error(f"Failed to validate MCP server at {url}: {e}")
        return {"status": "disconnected", "error": str(e), "url": url}


async def check_registered_servers(
    filename: str, bearer_token: Optional[str] = None
) -> Dict[str, Dict]:
    """
    Check connectivity of all registered servers using existing utilities.

    Args:
        filename: Path to server cache file (unused but kept for API consistency)

    Returns:
        Dict mapping server URLs to their status/tools
    """
    results = {}

    for key, server in SERVERS.servers.items():
        url = str(server)
        try:
            tools = await _check_mcp_connectivity(
                url, timeout=5.0, bearer_token=bearer_token
            )
            results[url] = {"status": "connected", "tools": tools}
        except Exception as e:
            results[url] = {"status": "disconnected", "error": str(e)}

    return results


def delete_registered_server(filename: str, url: str) -> Dict:
    """Delete a registered MCP server."""
    # Find the server by URL
    key_to_delete = None
    for key, server in SERVERS.servers.items():
        if str(server) == url or key == url:
            key_to_delete = key
            break

    if not key_to_delete:
        logger.warning(f"Server not found for deletion: {url}")
        return {
            "status": "not_found",
            "message": f"Server {url} not found in registered servers",
        }

    # Delete from in-memory dict
    deleted_server = SERVERS.servers.pop(key_to_delete)
    logger.info(f"Deleted server: {deleted_server.long_name()}")

    # Save to file
    if filename:
        try:
            with open(filename, "w") as f:
                f.write(SERVERS.model_dump_json(indent=4))
            logger.info(f"Saved updated server list to {filename}")
        except Exception as e:
            logger.error(f"Failed to save server list: {e}")
            return {
                "status": "error",
                "message": f"Deleted from memory but failed to save: {str(e)}",
            }

    return {
        "status": "deleted",
        "message": f"Successfully deleted server: {deleted_server.long_name()}",
    }


async def get_registered_servers(filename: str, request: Request = None) -> Dict:
    """
    Get list of all registered MCP servers and their status.

    This endpoint aggregates server info and checks connectivity
    using existing validation utilities.

    Args:
        filename: Path to server cache file
        request: Optional FastAPI Request object to extract bearer token from headers
    """
    # Extract bearer token from request headers if provided
    bearer_token = extract_bearer_token_from_headers(request) if request else None

    # Get connectivity status for all servers
    statuses = await check_registered_servers(filename, bearer_token=bearer_token)

    # Build response with server info and status
    servers = []
    for key, server in SERVERS.servers.items():
        url = str(server)
        status_info = statuses.get(url, {"status": "unknown"})

        servers.append(
            {
                "id": key,
                "url": url,
                "name": server.name,
                **status_info,
            }
        )

    return {"servers": servers}


async def validate_mcp_server_endpoint(
    filename: str, request: Request, data: ValidateMCPServerRequest
):
    """
    Validate and register an MCP server URL.
    Called by the frontend when a user adds a new tool server.

    This endpoint uses the existing system_utils.check_url_exists() and
    mcp_workbench_utils for validation.
    """
    # Extract bearer token from request headers
    bearer_token = extract_bearer_token_from_headers(request)

    # Get client info for logging
    client_info = get_client_info(request)
    logger.info(f"validate request from {client_info} for MCP server: {data.url}")

    result = await validate_and_register_mcp_server(
        filename, data.url, data.name, bearer_token
    )

    logger.info(f"Validate result: {result}")

    # Return the updated server list so frontend can refresh
    # This ensures the new server appears in the UI
    if result.get("status") == "connected":
        result["all_servers"] = list(SERVERS.servers.keys())

    return result


async def delete_mcp_server_endpoint(
    filename: str, request: Request, data: DeleteMCPServerRequest
):
    """Delete a registered MCP server."""
    client_info = get_client_info(request)
    logger.info(f"Delete request from {client_info} for MCP server: {data.url}")

    result = delete_registered_server(filename, data.url)

    logger.info(f"Delete result: {result}")

    return result


def list_server_urls(bearer_token: Optional[str] = None) -> list[str]:
    server_urls = []
    invalid_keys = []
    for key, server in SERVERS.servers.items():
        try:
            validated_server = check_server_paths(
                f"{server}", bearer_token=bearer_token
            )
            if validated_server:
                server_urls.append(f"{server}")
            else:
                logger.info(
                    f"Previously cached URL is no longer valid - removing {server.long_name()} from cache"
                )
                invalid_keys.append(key)
        except Exception as e:
            logger.warning(
                f"Error validating cached server {server.long_name()}: {e}. Removing from cache."
            )
            invalid_keys.append(key)

    for key in invalid_keys:
        SERVERS.servers.pop(key)

    # Validate URL format but don't crash - just log warnings
    if not server_urls:
        logger.warning(
            "No valid MCP server URLs found. Tools from MCP servers will not be available."
        )
    else:
        for url in server_urls:
            if not url.endswith("/mcp"):
                logger.warning(
                    f"Server URL {url} does not end with /mcp - this may cause connection issues"
                )

    return server_urls


async def list_server_tools(urls: list[str], bearer_token: Optional[str] = None):
    tool_list = []
    mcp_server_tool_list = await list_mcp_tools_direct(
        urls=urls, bearer_token=bearer_token
    )

    for server, tools in mcp_server_tool_list.items():
        # Check if tools is an error dict instead of a list
        if isinstance(tools, dict) and "error" in tools:
            logger.warning(
                f"Skipping MCP server {server} due to error: {tools['error']}"
            )
            continue

        logger.trace(f"MCP Server: {server}")
        for tool in tools:
            name = tool["name"]
            logger.trace(f"\tTool: {name}")
            tool_list.append((name, server))

    return tool_list


def try_get_public_hostname():
    import socket

    hostname = socket.gethostname()
    try:
        public_hostname = hostname + "-pub"
        host = socket.gethostbyname(public_hostname)
        hostname = public_hostname
    except socket.gaierror as e:
        try:
            host = socket.gethostbyname(hostname)
        except socket.gaierror as e:
            host = "127.0.0.1"

    return hostname, host
