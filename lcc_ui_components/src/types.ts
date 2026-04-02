//#############################################################################
// Copyright 2025-2026 Lawrence Livermore National Security, LLC.
// See the top-level LICENSE file for details.
//
// SPDX-License-Identifier: Apache-2.0
//#############################################################################

import { Dispatch, SetStateAction } from 'react';

// ============================================================================
// Settings Types
// ============================================================================

export interface ToolServer {
  id: string;
  url: string;
  name?: string; // Optional display name
  scope?: ToolServerScope;
}

export type ToolServerScope = 'backend' | 'local';
export type ToolExecutionScope = 'backend' | 'local';

export interface MCPToolDefinition {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export interface MCPConnectivityResult {
  status: 'connected' | 'disconnected';
  url?: string;
  tools?: MCPToolDefinition[];
  error?: string;
}

export type ReasoningEffort = 'low' | 'medium' | 'high';

export interface OrchestratorSettings {
  backend: string;
  useCustomUrl: boolean;
  customUrl?: string;
  model: string;
  reasoningEffort: ReasoningEffort;
  useCustomModel?: boolean;
  apiKey: string;
  backendLabel: string;
  toolServers?: ToolServer[];

  // RSA settings
  useRsa?: boolean;
  rsaMode?: 'standalone' | 'rag';
  rsaN?: number;
  rsaK?: number;
  rsaT?: number;
}

export interface BackendOption {
  value: string;
  label: string;
  defaultUrl: string;
  models: string[];
}

export interface MoleculeNameOption {
  value: string;
  label: string;
}

export interface SettingsButtonProps {
  onClick?: () => void;
  onSettingsChange?: (settings: OrchestratorSettings) => void;
  onServerAdded?: () => void;
  onServerRemoved?: () => void;
  initialSettings?: Partial<OrchestratorSettings>;
  username?: string;
  className?: string;
  httpServerUrl: string;
}

export interface LocalMcpProxyRequest {
  requestId?: string;
  requestKind?: 'list-tools' | 'call-tool';
  servers?: string[];
  serverUrl?: string;
  toolName?: string;
  arguments?: Record<string, unknown>;
}

export interface LocalMcpProxyResponse {
  action: 'local-mcp-response';
  requestId: string;
  ok: boolean;
  result?: Record<string, unknown>;
  error?: string;
}

// ============================================================================
// Sidebar Types
// ============================================================================

export interface SidebarMessage {
  id: number;
  timestamp: string;
  message: string;
  smiles: string | null;
  source: string;
}

export interface VisibleSources {
  [key: string]: boolean;
}

export interface SidebarState {
  messages: SidebarMessage[];
  setMessages: Dispatch<SetStateAction<SidebarMessage[]>>;
  sourceFilterOpen: boolean;
  setSourceFilterOpen: Dispatch<SetStateAction<boolean>>;
  visibleSources: VisibleSources;
  setVisibleSources: Dispatch<SetStateAction<VisibleSources>>;
}

export interface SidebarProps extends SidebarState {
  setSidebarOpen: Dispatch<SetStateAction<boolean>>;
  rdkitModule?: any; // Optional RDKit module (for backwards compatibility)
}

// ============================================================================
// Markdown Types
// ============================================================================

export interface MarkdownTextProps {
  text: string;
  className?: string;
  collapsibleCodeBlocks?: boolean;
  defaultCollapsedCodeBlocks?: boolean;
  codeBlockCollapseThreshold?: number;
}
