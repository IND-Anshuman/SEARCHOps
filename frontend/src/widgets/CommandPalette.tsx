import React, { useState, useEffect } from 'react';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { WorkbenchView } from '../shared/types/workbench';
import { 
  Search, 
  X, 
  GitFork, 
  Share2, 
  Database,
  FileText
} from 'lucide-react';

export const CommandPalette: React.FC = () => {
  const { 
    isCommandPaletteOpen, 
    setCommandPaletteOpen, 
    setActiveView, 
    inspectItem 
  } = useWorkbenchStore();

  const { 
    nodes, 
    entities, 
    chunks 
  } = useExecutionStore();

  const [query, setQuery] = useState('');

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setCommandPaletteOpen(!isCommandPaletteOpen);
      }
      if (e.key === 'Escape' && isCommandPaletteOpen) {
        setCommandPaletteOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isCommandPaletteOpen, setCommandPaletteOpen]);

  if (!isCommandPaletteOpen) return null;

  const navigateTo = (view: WorkbenchView) => {
    setActiveView(view);
    setCommandPaletteOpen(false);
  };

  const inspectAndClose = (type: any, data: any) => {
    inspectItem(type, data);
    setCommandPaletteOpen(false);
  };

  const filteredNodes = nodes.filter(n => n.label.toLowerCase().includes(query.toLowerCase()));
  const filteredEntities = entities.filter(e => e.name.toLowerCase().includes(query.toLowerCase()));
  const filteredChunks = chunks.filter(c => c.documentTitle.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm z-50 flex items-start justify-center pt-20 px-4">
      <div className="bg-slate-900 border border-slate-700 w-full max-w-2xl rounded-lg shadow-2xl overflow-hidden font-mono text-xs select-none">
        {/* Search Input Bar */}
        <div className="h-12 border-b border-slate-800 px-3 flex items-center justify-between bg-slate-950">
          <div className="flex items-center space-x-2 flex-1">
            <Search className="w-4 h-4 text-emerald-400 shrink-0" />
            <input 
              type="text" 
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Type to search entities, nodes, or system commands..."
              className="w-full bg-transparent text-slate-100 placeholder-slate-500 focus:outline-none text-sm"
            />
          </div>
          <button 
            onClick={() => setCommandPaletteOpen(false)}
            className="p-1 text-slate-500 hover:text-slate-300 rounded"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Results List Area */}
        <div className="max-h-96 overflow-y-auto p-2 space-y-3">
          {/* Quick Navigation Commands */}
          <div>
            <div className="px-2 py-1 text-slate-500 font-bold text-[10px] uppercase">Views</div>
            <div className="grid grid-cols-2 gap-1">
              <button 
                onClick={() => navigateTo('chat')}
                className="flex items-center space-x-2 px-2.5 py-1.5 rounded bg-slate-950/60 hover:bg-slate-800 text-slate-200 transition text-left"
              >
                <GitFork className="w-3.5 h-3.5 text-emerald-400" />
                <span>Research Workspace</span>
              </button>
              <button 
                onClick={() => navigateTo('execution')}
                className="flex items-center space-x-2 px-2.5 py-1.5 rounded bg-slate-950/60 hover:bg-slate-800 text-slate-200 transition text-left"
              >
                <GitFork className="w-3.5 h-3.5 text-cyan-400" />
                <span>LangGraph Execution</span>
              </button>
              <button 
                onClick={() => navigateTo('graph')}
                className="flex items-center space-x-2 px-2.5 py-1.5 rounded bg-slate-950/60 hover:bg-slate-800 text-slate-200 transition text-left"
              >
                <Share2 className="w-3.5 h-3.5 text-indigo-400" />
                <span>Knowledge Graph</span>
              </button>
              <button 
                onClick={() => navigateTo('reports')}
                className="flex items-center space-x-2 px-2.5 py-1.5 rounded bg-slate-950/60 hover:bg-slate-800 text-slate-200 transition text-left"
              >
                <FileText className="w-3.5 h-3.5 text-amber-400" />
                <span>Synthesis Report</span>
              </button>
            </div>
          </div>

          {/* LangGraph Nodes */}
          {filteredNodes.length > 0 && (
            <div>
              <div className="px-2 py-1 text-slate-500 font-bold text-[10px] uppercase">Execution Nodes</div>
              <div className="space-y-1">
                {filteredNodes.map(node => (
                  <button
                    key={node.id}
                    onClick={() => inspectAndClose('node', node)}
                    className="w-full flex items-center justify-between px-2.5 py-1.5 rounded bg-slate-950/40 hover:bg-slate-800 text-slate-200 transition"
                  >
                    <div className="flex items-center space-x-2">
                      <GitFork className="w-3.5 h-3.5 text-cyan-400" />
                      <span>{node.label}</span>
                    </div>
                    <span className="text-[10px] text-slate-500 font-mono">{node.latencyMs}ms</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Knowledge Entities */}
          {filteredEntities.length > 0 && (
            <div>
              <div className="px-2 py-1 text-slate-500 font-bold text-[10px] uppercase">GraphRAG Entities</div>
              <div className="space-y-1">
                {filteredEntities.map(entity => (
                  <button
                    key={entity.id}
                    onClick={() => inspectAndClose('entity', entity)}
                    className="w-full flex items-center justify-between px-2.5 py-1.5 rounded bg-slate-950/40 hover:bg-slate-800 text-slate-200 transition"
                  >
                    <div className="flex items-center space-x-2">
                      <Share2 className="w-3.5 h-3.5 text-indigo-400" />
                      <span>{entity.name}</span>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
export default CommandPalette;
