import React, { useState } from 'react';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { 
  Share2, 
  Search, 
  HelpCircle,
  Network
} from 'lucide-react';

export const KnowledgeGraphExplorer: React.FC = () => {
  const { entities, graphEdges, job } = useExecutionStore();
  const { inspectItem } = useWorkbenchStore();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const filteredEntities = entities.filter(node => 
    node.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    node.type.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Math layout: circular distribution centered in 500x500 viewport
  const width = 600;
  const height = 400;
  const cx = width / 2;
  const cy = height / 2;
  const radius = 160;

  // Map nodes to calculated coordinates
  const nodePositions = new Map<string, { x: number, y: number }>();
  filteredEntities.forEach((node, idx) => {
    const angle = (idx / filteredEntities.length) * 2 * Math.PI;
    const x = cx + radius * Math.cos(angle);
    const y = cy + radius * Math.sin(angle);
    nodePositions.set(node.canonicalId || node.id, { x, y });
  });

  const handleNodeClick = (node: any) => {
    setSelectedNodeId(node.canonicalId || node.id);
    inspectItem('entity', node);
  };

  const getEntityColor = (type: string) => {
    switch (type.toLowerCase()) {
      case 'technology': return '#10b981'; // emerald
      case 'concept': return '#06b6d4'; // cyan
      case 'organization': return '#6366f1'; // indigo
      case 'person': return '#f59e0b'; // amber
      default: return '#8b5cf6'; // purple
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-slate-950 text-xs font-mono select-none overflow-hidden">
      {/* Search and Control Bar */}
      <div className="bg-slate-900 border-b border-slate-800 p-3 flex flex-wrap items-center justify-between gap-3 shrink-0">
        <div className="flex items-center space-x-2 flex-1 min-w-[200px]">
          <Search className="w-4 h-4 text-slate-500 shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search Neo4j entities by name or type..."
            className="w-full max-w-sm bg-slate-950 border border-slate-850 focus:border-emerald-500/50 rounded px-2.5 py-1 text-slate-200 placeholder-slate-600 focus:outline-none"
          />
        </div>
        
        <div className="flex items-center space-x-3 text-[10px] text-slate-500">
          <div className="flex items-center space-x-1">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
            <span>Tech</span>
          </div>
          <div className="flex items-center space-x-1">
            <span className="w-2.5 h-2.5 rounded-full bg-cyan-500" />
            <span>Concept</span>
          </div>
          <div className="flex items-center space-x-1">
            <span className="w-2.5 h-2.5 rounded-full bg-indigo-500" />
            <span>Org</span>
          </div>
          <div className="flex items-center space-x-1">
            <span className="w-2.5 h-2.5 rounded-full bg-amber-500" />
            <span>Person</span>
          </div>
        </div>
      </div>

      {/* SVG Canvas Area */}
      <div className="flex-1 overflow-auto flex items-center justify-center p-4 bg-slate-950 relative">
        {entities.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-12 border border-dashed border-slate-850 rounded-lg text-slate-600 bg-slate-950/60 max-w-md">
            <Network className="w-8 h-8 text-slate-750 mb-2" />
            <p className="font-sans text-xs">No graph entities mined.</p>
            <p className="font-sans text-[10px] text-slate-600 mt-1 text-center">Entities extracted during LangGraph processing will appear as a live Neo4j subgraph link map.</p>
          </div>
        ) : (
          <div className="relative border border-slate-900 bg-slate-900/30 rounded-lg p-2 max-w-full overflow-hidden shadow-inner">
            <svg 
              width={width} 
              height={height} 
              className="bg-slate-950/20 overflow-visible"
            >
              {/* Draw Edges */}
              {graphEdges.map((edge) => {
                const sourcePos = nodePositions.get(edge.source);
                const targetPos = nodePositions.get(edge.target);

                if (!sourcePos || !targetPos) return null;

                const isSelected = selectedNodeId === edge.source || selectedNodeId === edge.target;

                return (
                  <g key={edge.id}>
                    <line
                      x1={sourcePos.x}
                      y1={sourcePos.y}
                      x2={targetPos.x}
                      y2={targetPos.y}
                      stroke={isSelected ? '#10b981' : '#334155'}
                      strokeWidth={isSelected ? 1.8 : 0.8}
                      strokeDasharray={isSelected ? '0' : '3 3'}
                      className="transition-all duration-300"
                    />
                    {/* Tiny edge type label on hover */}
                    {isSelected && (
                      <text
                        x={(sourcePos.x + targetPos.x) / 2}
                        y={(sourcePos.y + targetPos.y) / 2 - 3}
                        fill="#94a3b8"
                        fontSize="8px"
                        textAnchor="middle"
                        className="bg-slate-950 px-1 font-mono"
                      >
                        {edge.relation_type}
                      </text>
                    )}
                  </g>
                );
              })}

              {/* Draw Nodes */}
              {filteredEntities.map((node) => {
                const pos = nodePositions.get(node.canonicalId || node.id);
                if (!pos) return null;

                const isSelected = selectedNodeId === (node.canonicalId || node.id);
                const color = getEntityColor(node.type);

                return (
                  <g 
                    key={node.id} 
                    transform={`translate(${pos.x}, ${pos.y})`}
                    onClick={() => handleNodeClick(node)}
                    className="cursor-pointer group"
                  >
                    <circle
                      r={isSelected ? 10 : 7}
                      fill="#0f172a"
                      stroke={color}
                      strokeWidth={isSelected ? 2.5 : 1.5}
                      className="transition-all duration-300 hover:scale-125"
                    />
                    <text
                      y={-12}
                      textAnchor="middle"
                      fill={isSelected ? '#10b981' : '#e2e8f0'}
                      fontSize="9px"
                      fontWeight={isSelected ? 'bold' : 'normal'}
                      className="font-sans pointer-events-none drop-shadow-md select-none group-hover:fill-emerald-400 transition"
                    >
                      {node.name}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
        )}
      </div>
    </div>
  );
};
export default KnowledgeGraphExplorer;
