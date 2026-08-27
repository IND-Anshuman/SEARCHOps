import { useState, useEffect } from 'react';
import { WorkbenchView, ViewportMode } from '../types/workbench';

export interface WorkbenchState {
  activeView: WorkbenchView;
  viewportMode: ViewportMode;
  isSidebarOpen: boolean;
  isInspectorOpen: boolean;
  isConsoleOpen: boolean;
  activeConsoleTab: 'logs' | 'events' | 'errors' | 'websocket' | 'network' | 'workers';
  isCommandPaletteOpen: boolean;
  isNotificationCenterOpen: boolean;
  inspectorSelection: {
    type: 'node' | 'entity' | 'chunk' | 'report' | 'citation' | 'execution' | 'prompt' | 'none';
    data: any;
  };

  setActiveView: (view: WorkbenchView) => void;
  setViewportMode: (mode: ViewportMode) => void;
  toggleSidebar: () => void;
  toggleInspector: () => void;
  toggleConsole: () => void;
  setActiveConsoleTab: (tab: 'logs' | 'events' | 'errors' | 'websocket' | 'network' | 'workers') => void;
  setCommandPaletteOpen: (open: boolean) => void;
  setNotificationCenterOpen: (open: boolean) => void;
  inspectItem: (type: 'node' | 'entity' | 'chunk' | 'report' | 'citation' | 'execution' | 'prompt', data: any) => void;
  closeInspector: () => void;
}

let workbenchState: WorkbenchState = {
  activeView: 'chat',
  viewportMode: 'developer',
  isSidebarOpen: true,
  isInspectorOpen: false,
  isConsoleOpen: true,
  activeConsoleTab: 'logs',
  isCommandPaletteOpen: false,
  isNotificationCenterOpen: false,
  inspectorSelection: {
    type: 'none',
    data: null,
  },

  setActiveView: (view: WorkbenchView) => {
    workbenchState.activeView = view;
    emitChange();
  },
  setViewportMode: (mode: ViewportMode) => {
    workbenchState.viewportMode = mode;
    emitChange();
  },
  toggleSidebar: () => {
    workbenchState.isSidebarOpen = !workbenchState.isSidebarOpen;
    emitChange();
  },
  toggleInspector: () => {
    workbenchState.isInspectorOpen = !workbenchState.isInspectorOpen;
    emitChange();
  },
  toggleConsole: () => {
    workbenchState.isConsoleOpen = !workbenchState.isConsoleOpen;
    emitChange();
  },
  setActiveConsoleTab: (tab) => {
    workbenchState.activeConsoleTab = tab;
    emitChange();
  },
  setCommandPaletteOpen: (open: boolean) => {
    workbenchState.isCommandPaletteOpen = open;
    emitChange();
  },
  setNotificationCenterOpen: (open: boolean) => {
    workbenchState.isNotificationCenterOpen = open;
    emitChange();
  },
  inspectItem: (type, data) => {
    workbenchState.inspectorSelection = { type, data };
    workbenchState.isInspectorOpen = true;
    emitChange();
  },
  closeInspector: () => {
    workbenchState.inspectorSelection = { type: 'none', data: null };
    workbenchState.isInspectorOpen = false;
    emitChange();
  },
};

const listeners = new Set<() => void>();

function emitChange() {
  listeners.forEach(listener => listener());
}

export function useWorkbenchStore(): WorkbenchState {
  const [state, setState] = useState<WorkbenchState>(workbenchState);

  useEffect(() => {
    const listener = () => setState({ ...workbenchState });
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  return state;
}
