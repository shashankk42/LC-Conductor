//#############################################################################
// Copyright 2025-2026 Lawrence Livermore National Security, LLC.
// See the top-level LICENSE file for details.
//
// SPDX-License-Identifier: Apache-2.0
//#############################################################################

// Main entry point for LC-Conductor components

// Components
export { SettingsButton } from './SettingsButton.js';
export { ReasoningSidebar, useSidebarState } from './ReasoningSidebar.js';
export { MarkdownText } from './MarkdownText.js';
export { RsaSettingsPanel } from './RsaSettingsPanel.js';

// Constants
export { BACKEND_OPTIONS } from './constants.js';
export {
  callLocalMcpTool,
  checkLocalMcpServerConnectivity,
  handleLocalMcpProxyRequest,
  listLocalMcpTools,
  normalizeMcpUrl,
} from './localMcp.js';

// Types
export type {
  // Settings types
  ToolServer,
  ToolServerScope,
  ToolExecutionScope,
  MCPToolDefinition,
  MCPConnectivityResult,
  ReasoningEffort,
  OrchestratorSettings,
  BackendOption,
  SettingsButtonProps,
  LocalMcpProxyRequest,
  LocalMcpProxyResponse,

  // Sidebar types
  SidebarMessage,
  SidebarState,
  SidebarProps,
  VisibleSources,

  // Markdown types
  MarkdownTextProps,
} from './types.js';
