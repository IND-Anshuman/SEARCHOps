import React, { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { useExecutionStore } from '../shared/store/useExecutionStore';
import { useWorkbenchStore } from '../shared/store/useWorkbenchStore';
import { str, num } from '../shared/lib/safe';
import { API_BASE } from '../shared/store/executionStore';
import { apiFetch } from '../shared/lib/api';
import type { EvidenceGap } from '../shared/types/workbench';
import {
  Search,
  ZoomIn,
  ZoomOut,
  Maximize2,
  RotateCcw,
  Sparkles,
  ArrowRight,
  X,
  Layers,
  Compass,
  CheckCircle2,
  BookOpen,
  HelpCircle,
} from 'lucide-react';

interface SimulationNode {
  id: string;
  canonicalId: string;
  name: string;
  type: string;
  summary: string;
  isFrontier: boolean;
  priority?: number;
  targetQuery?: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  degree: number;
}

interface SimulationLink {
  id: string;
  source: string;
  target: string;
  relation_type: string;
  isFrontier: boolean;
}

type NodeShape = 'disc' | 'ring' | 'square' | 'diamond' | 'frontier';

const TYPE_SHAPE: Record<string, NodeShape> = {
  concept: 'disc',
  technology: 'ring',
  metric: 'square',
  organization: 'diamond',
  person: 'diamond',
  frontier: 'frontier',
};

export const KnowledgeGraphExplorer: React.FC = () => {
  const { entities, graphEdges, job, gaps: storeGaps } = useExecutionStore();
  const { inspectItem, setActiveView } = useWorkbenchStore();

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [showFrontiers, setShowFrontiers] = useState(true);
  const [gaps, setGaps] = useState<EvidenceGap[]>(() => storeGaps || []);
  const [loadingGaps, setLoadingGaps] = useState(false);

  // Sync store gaps whenever they update
  useEffect(() => {
    if (storeGaps && Array.isArray(storeGaps) && storeGaps.length > 0) {
      setGaps(storeGaps);
    }
  }, [storeGaps]);

  // Canvas Viewport transform: pan (x, y) & zoom (k)
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 1 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const [draggedNodeId, setDraggedNodeId] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 900, height: 600 });
  const [copiedQuery, setCopiedQuery] = useState(false);

  // Resize observer for dynamic canvas scaling
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        setDimensions({
          width: Math.max(entry.contentRect.width, 600),
          height: Math.max(entry.contentRect.height, 400),
        });
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // Fetch coverage gaps for Frontier Node synthesis
  useEffect(() => {
    const jobId = job.id;
    if (!jobId) return;

    let cancelled = false;
    setLoadingGaps(true);

    void (async () => {
      try {
        const res = await apiFetch(`${API_BASE}/api/v1/derived/${jobId}/next-steps`);
        if (!res.ok) {
          setLoadingGaps(false);
          return;
        }
        const data = (await res.json()) as { gaps?: EvidenceGap[] };
        if (!cancelled && data.gaps && Array.isArray(data.gaps)) {
          setGaps(data.gaps);
        }
      } catch {
        /* fail gracefully when offline */
      } finally {
        if (!cancelled) setLoadingGaps(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [job.id]);

  // Build Unified Graph Model: Grounded Entities + Frontier Nodes
  const { initialNodes, initialLinks } = useMemo(() => {
    const nodeMap = new Map<string, SimulationNode>();
    const linkList: SimulationLink[] = [];

    // 1. Ingest grounded entities
    entities.forEach((ent, idx) => {
      const id = ent.canonicalId || ent.id || `ent-${idx}`;
      nodeMap.set(id, {
        id,
        canonicalId: ent.canonicalId || id,
        name: ent.name || 'Unnamed Concept',
        type: ent.type || 'concept',
        summary: ent.summary || '',
        isFrontier: false,
        x: 0,
        y: 0,
        vx: 0,
        vy: 0,
        radius: 20,
        degree: 0,
      });
    });

    // 2. Ingest graph relations
    graphEdges.forEach((edge, idx) => {
      if (nodeMap.has(edge.source) && nodeMap.has(edge.target)) {
        linkList.push({
          id: edge.id || `edge-${idx}`,
          source: edge.source,
          target: edge.target,
          relation_type: edge.relation_type || 'related_to',
          isFrontier: false,
        });
        const s = nodeMap.get(edge.source)!;
        const t = nodeMap.get(edge.target)!;
        s.degree += 1;
        t.degree += 1;
      }
    });

    // Compute radii based on degree centrality
    nodeMap.forEach((node) => {
      node.radius = Math.min(32, Math.max(18, 18 + node.degree * 3.5));
    });

    // 3. Synthesize Frontier Nodes from Coverage Gaps
    if (showFrontiers && gaps.length > 0) {
      gaps.forEach((gap, gIdx) => {
        const frontierId = `frontier-${gIdx}`;
        const queryLower = (gap.targetQuery || gap.description || '').toLowerCase();

        // Connect frontier node to the most relevant grounded entities by keyword matching
        const connectedEntityIds: string[] = [];
        entities.forEach((ent) => {
          const nameLower = (ent.name || '').toLowerCase();
          if (nameLower && (queryLower.includes(nameLower) || gap.description.toLowerCase().includes(nameLower))) {
            connectedEntityIds.push(ent.canonicalId || ent.id);
          }
        });

        // If no direct keyword match, link to highest degree anchor entity or first entity
        if (connectedEntityIds.length === 0 && entities.length > 0) {
          const anchor = [...nodeMap.values()].sort((a, b) => b.degree - a.degree)[0];
          if (anchor) connectedEntityIds.push(anchor.id);
        }

        nodeMap.set(frontierId, {
          id: frontierId,
          canonicalId: frontierId,
          name: gap.targetQuery || gap.description.slice(0, 36),
          type: 'frontier',
          summary: gap.description,
          isFrontier: true,
          priority: gap.priority || 1,
          targetQuery: gap.targetQuery || gap.description,
          x: 0,
          y: 0,
          vx: 0,
          vy: 0,
          radius: 24,
          degree: connectedEntityIds.length,
        });

        connectedEntityIds.forEach((targetEntId, cIdx) => {
          linkList.push({
            id: `frontier-link-${gIdx}-${cIdx}`,
            source: targetEntId,
            target: frontierId,
            relation_type: 'gap_vector',
            isFrontier: true,
          });
        });
      });
    }

    return {
      initialNodes: Array.from(nodeMap.values()),
      initialLinks: linkList,
    };
  }, [entities, graphEdges, gaps, showFrontiers]);

  // Active Simulation State with stable coordinates
  const [nodes, setNodes] = useState<SimulationNode[]>([]);
  const [links, setLinks] = useState<SimulationLink[]>([]);

  // Initialize and run Force-Directed Physics Simulation
  useEffect(() => {
    if (initialNodes.length === 0) {
      setNodes([]);
      setLinks([]);
      return;
    }

    const cx = dimensions.width / 2;
    const cy = dimensions.height / 2;
    const count = initialNodes.length;

    // Seed initial positions radially
    const initializedNodes: SimulationNode[] = initialNodes.map((node, i) => {
      const angle = (i / count) * 2 * Math.PI;
      const dist = node.isFrontier ? 230 + (i % 3) * 25 : 80 + (i % 2) * 50;
      return {
        ...node,
        x: cx + dist * Math.cos(angle) + (Math.random() - 0.5) * 20,
        y: cy + dist * Math.sin(angle) + (Math.random() - 0.5) * 20,
        vx: 0,
        vy: 0,
      };
    });

    const activeLinks = [...initialLinks];

    // Run 100 ticks of lightweight Verlet force integration
    for (let tick = 0; tick < 110; tick++) {
      // 1. Repulsion between all nodes
      for (let i = 0; i < initializedNodes.length; i++) {
        for (let j = i + 1; j < initializedNodes.length; j++) {
          const a = initializedNodes[i];
          const b = initializedNodes[j];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const distSq = dx * dx + dy * dy || 1;
          const dist = Math.sqrt(distSq);
          const minDist = a.radius + b.radius + 35;

          if (dist < minDist || dist < 240) {
            const force = (minDist * 70) / (distSq + 10);
            const fx = (dx / dist) * force;
            const fy = (dy / dist) * force;
            a.vx -= fx;
            a.vy -= fy;
            b.vx += fx;
            b.vy += fy;
          }
        }
      }

      // 2. Link Spring Attraction
      for (const link of activeLinks) {
        const sourceNode = initializedNodes.find((n) => n.id === link.source);
        const targetNode = initializedNodes.find((n) => n.id === link.target);
        if (!sourceNode || !targetNode) continue;

        const dx = targetNode.x - sourceNode.x;
        const dy = targetNode.y - sourceNode.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const targetDist = link.isFrontier ? 150 : 100;
        const force = (dist - targetDist) * 0.045;

        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;

        sourceNode.vx += fx;
        sourceNode.vy += fy;
        targetNode.vx -= fx;
        targetNode.vy -= fy;
      }

      // 3. Center Gravity & Frontier Orbit Push
      for (const node of initializedNodes) {
        const dx = node.x - cx;
        const dy = node.y - cy;
        const distFromCenter = Math.sqrt(dx * dx + dy * dy) || 1;

        if (node.isFrontier) {
          // Keep frontier nodes in outer orbit (~240px)
          const orbitForce = (distFromCenter - 240) * 0.03;
          node.vx -= (dx / distFromCenter) * orbitForce;
          node.vy -= (dy / distFromCenter) * orbitForce;
        } else {
          // Grounded nodes gravitate toward center
          node.vx -= (dx / distFromCenter) * 0.02 * (node.degree + 1);
          node.vy -= (dy / distFromCenter) * 0.02 * (node.degree + 1);
        }

        // Apply friction damping
        node.vx *= 0.72;
        node.vy *= 0.72;
        node.x += node.vx;
        node.y += node.vy;
      }
    }

    setNodes(initializedNodes);
    setLinks(activeLinks);
  }, [initialNodes, initialLinks, dimensions]);

  // Subgraph & Prerequisite Highlighting
  const { connectedNodeIds, connectedEdgeIds } = useMemo(() => {
    if (!selectedNodeId) {
      return {
        connectedNodeIds: new Set<string>(),
        connectedEdgeIds: new Set<string>(),
      };
    }

    const nIds = new Set<string>([selectedNodeId]);
    const eIds = new Set<string>();

    links.forEach((l) => {
      if (l.source === selectedNodeId) {
        nIds.add(l.target);
        eIds.add(l.id);
      } else if (l.target === selectedNodeId) {
        nIds.add(l.source);
        eIds.add(l.id);
      }
    });

    return { connectedNodeIds: nIds, connectedEdgeIds: eIds };
  }, [selectedNodeId, links]);

  // Selected Node Details
  const selectedNode = useMemo(() => {
    if (!selectedNodeId) return null;
    return nodes.find((n) => n.id === selectedNodeId) || null;
  }, [selectedNodeId, nodes]);

  // Filtered nodes matching query
  const searchMatches = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return new Set<string>();
    const matches = new Set<string>();
    nodes.forEach((n) => {
      if (n.name.toLowerCase().includes(q) || n.type.toLowerCase().includes(q) || n.summary.toLowerCase().includes(q)) {
        matches.add(n.id);
      }
    });
    return matches;
  }, [searchQuery, nodes]);

  // 1-Click Action: Launch Research from Frontier Node
  const handleLaunchFrontierResearch = (queryText: string) => {
    if (!queryText) return;
    sessionStorage.setItem('searchops:prefill_query', queryText);
    setCopiedQuery(true);
    setTimeout(() => setCopiedQuery(false), 2000);
    setActiveView('chat');
  };

  // Zoom & Pan Handlers
  const handleZoom = (factor: number) => {
    setTransform((prev) => ({
      ...prev,
      k: Math.max(0.4, Math.min(3.0, prev.k * factor)),
    }));
  };

  const handleResetView = () => {
    setTransform({ x: 0, y: 0, k: 1 });
    setSelectedNodeId(null);
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget || (e.target as HTMLElement).tagName === 'svg') {
      setIsPanning(true);
      setPanStart({ x: e.clientX - transform.x, y: e.clientY - transform.y });
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isPanning) {
      setTransform((prev) => ({
        ...prev,
        x: e.clientX - panStart.x,
        y: e.clientY - panStart.y,
      }));
    } else if (draggedNodeId) {
      // Direct node dragging
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const mouseX = (e.clientX - rect.left - transform.x) / transform.k;
      const mouseY = (e.clientY - rect.top - transform.y) / transform.k;

      setNodes((prev) =>
        prev.map((n) => (n.id === draggedNodeId ? { ...n, x: mouseX, y: mouseY } : n)),
      );
    }
  };

  const handleMouseUp = () => {
    setIsPanning(false);
    setDraggedNodeId(null);
  };

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.08 : 0.92;
    setTransform((prev) => ({
      ...prev,
      k: Math.max(0.35, Math.min(3.0, prev.k * zoomFactor)),
    }));
  }, []);

  const handleNodeClick = (node: SimulationNode, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedNodeId(node.id);
    if (!node.isFrontier) {
      inspectItem('entity', {
        id: node.id,
        canonicalId: node.canonicalId,
        name: node.name,
        type: node.type,
        summary: node.summary,
      });
    }
  };

  return (
    <div
      ref={containerRef}
      className="flex-1 flex flex-col h-full bg-paper text-ink overflow-hidden select-none relative"
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onWheel={handleWheel}
    >
      {/* ── Top Floating Toolbar ───────────────────────────────────────────── */}
      <div className="absolute top-4 left-4 right-4 z-20 flex flex-wrap items-center justify-between gap-3 pointer-events-none">
        {/* Left: Search & Filter bar */}
        <div className="flex items-center gap-2 pointer-events-auto bg-paper-raised/90 header-gradient border border-rule card-beveled px-3 py-1.5 shadow-sm backdrop-blur-sm min-w-[260px] max-w-sm">
          <Search className="w-4 h-4 text-ink-faint shrink-0" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search entities & frontier gaps…"
            className="w-full bg-transparent text-label text-ink placeholder:text-ink-faint focus:outline-none"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="text-ink-faint hover:text-ink text-micro"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {/* Right: Controls & Viewport Tools */}
        <div className="flex items-center gap-2 pointer-events-auto">
          {/* Toggle Study Next Frontier Nodes */}
          <button
            type="button"
            onClick={() => setShowFrontiers((v) => !v)}
            title="Toggle Study Next & Coverage Gap nodes"
            className={`flex items-center gap-1.5 px-3 py-1.5 text-label border card-beveled transition-all tactile-btn shadow-sm ${
              showFrontiers
                ? 'bg-paper-raised border-rule-strong text-ink font-medium shadow-debossed'
                : 'bg-paper border-rule text-ink-faint hover:text-ink'
            }`}
          >
            <Sparkles className="w-3.5 h-3.5 text-ink" />
            <span className="hidden sm:inline">Study Next Frontier</span>
            {gaps.length > 0 && (
              <span className="text-micro font-mono bg-paper-inset px-1.5 py-0.2 rounded-full border border-rule">
                {gaps.length}
              </span>
            )}
          </button>

          {/* Zoom controls */}
          <div className="flex items-center border border-rule card-beveled bg-paper-raised/90 header-gradient shadow-sm overflow-hidden">
            <button
              type="button"
              onClick={() => handleZoom(1.2)}
              title="Zoom In"
              className="p-1.5 text-ink-faint hover:text-ink hover:bg-paper transition-colors"
            >
              <ZoomIn className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={() => handleZoom(0.8)}
              title="Zoom Out"
              className="p-1.5 text-ink-faint hover:text-ink hover:bg-paper transition-colors border-l border-rule"
            >
              <ZoomOut className="w-4 h-4" />
            </button>
            <button
              type="button"
              onClick={handleResetView}
              title="Reset View"
              className="p-1.5 text-ink-faint hover:text-ink hover:bg-paper transition-colors border-l border-rule"
            >
              <RotateCcw className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* ── Main Cartographic SVG Canvas ───────────────────────────────────── */}
      <div className="flex-1 w-full h-full cursor-grab active:cursor-grabbing overflow-hidden relative">
        {nodes.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center max-w-sm mx-auto py-12">
            <div className="w-14 h-14 rounded-full border border-dashed border-rule-strong flex items-center justify-center mb-3">
              <Compass className="w-6 h-6 text-ink-faint animate-pulse" />
            </div>
            <p className="font-display text-[18px] text-ink">No knowledge map yet</p>
            <p className="text-label text-ink-muted mt-1 leading-relaxed">
              Run a research job to extract verified entity relationships and map the research frontier.
            </p>
          </div>
        ) : (
          <svg
            width="100%"
            height="100%"
            className="w-full h-full block"
            role="img"
            aria-label="Interactive Cartographic Knowledge Graph"
          >
            <defs>
              {/* Soft Ink Drop Shadow */}
              <filter id="carto-shadow" x="-20%" y="-20%" width="140%" height="140%">
                <feDropShadow dx="0" dy="1.5" stdDeviation="2.5" floodColor="#000" floodOpacity="0.12" />
              </filter>
              <filter id="halo-glow" x="-30%" y="-30%" width="160%" height="160%">
                <feDropShadow dx="0" dy="0" stdDeviation="4" floodColor="currentColor" floodOpacity="0.3" />
              </filter>

              {/* Directional Arrowheads */}
              <marker
                id="arrowhead-grounded"
                viewBox="0 0 10 10"
                refX="24"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="currentColor" />
              </marker>

              <marker
                id="arrowhead-frontier"
                viewBox="0 0 10 10"
                refX="22"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 2 L 7 5 L 0 8 z" fill="currentColor" opacity="0.85" />
              </marker>

              {/* Cartographic Coordinate Grid Pattern */}
              <pattern id="carto-grid" width="60" height="60" patternUnits="userSpaceOnUse">
                <circle cx="30" cy="30" r="0.75" fill="currentColor" opacity="0.25" />
                <path d="M 60 0 L 0 0 0 60" fill="none" stroke="currentColor" strokeWidth="0.5" opacity="0.12" strokeDasharray="2 4" />
              </pattern>
            </defs>

            {/* Background Archival Coordinate Grid */}
            <rect width="100%" height="100%" fill="url(#carto-grid)" className="text-ink" />

            {/* Viewport Transform Group (Pan & Zoom) */}
            <g transform={`translate(${transform.x}, ${transform.y}) scale(${transform.k})`}>
              {/* Concentric Coordinate Rings */}
              <g opacity="0.08" className="text-ink" pointerEvents="none">
                <circle cx={dimensions.width / 2} cy={dimensions.height / 2} r="120" fill="none" stroke="currentColor" strokeWidth="1" strokeDasharray="3 3" />
                <circle cx={dimensions.width / 2} cy={dimensions.height / 2} r="240" fill="none" stroke="currentColor" strokeWidth="1" strokeDasharray="4 6" />
                <line x1={dimensions.width / 2 - 270} y1={dimensions.height / 2} x2={dimensions.width / 2 + 270} y2={dimensions.height / 2} stroke="currentColor" strokeWidth="0.8" />
                <line x1={dimensions.width / 2} y1={dimensions.height / 2 - 270} x2={dimensions.width / 2} y2={dimensions.height / 2 + 270} stroke="currentColor" strokeWidth="0.8" />
              </g>

              {/* ── 1. Graph Edges ───────────────────────────────────────── */}
              <g className="edges-layer">
                {links.map((link) => {
                  const sourceNode = nodes.find((n) => n.id === link.source);
                  const targetNode = nodes.find((n) => n.id === link.target);
                  if (!sourceNode || !targetNode) return null;

                  const isConnected =
                    !selectedNodeId || connectedEdgeIds.has(link.id);
                  const isHighlighted =
                    selectedNodeId && connectedEdgeIds.has(link.id);

                  const opacity = isConnected ? (isHighlighted ? 1.0 : 0.65) : 0.08;

                  return (
                    <g key={link.id} opacity={opacity} className="transition-opacity duration-200">
                      <line
                        x1={sourceNode.x}
                        y1={sourceNode.y}
                        x2={targetNode.x}
                        y2={targetNode.y}
                        stroke="currentColor"
                        strokeWidth={link.isFrontier ? 1.5 : isHighlighted ? 2.2 : 1.2}
                        strokeDasharray={link.isFrontier ? '5 4' : undefined}
                        markerEnd={link.isFrontier ? 'url(#arrowhead-frontier)' : 'url(#arrowhead-grounded)'}
                        className={link.isFrontier ? 'text-ink-muted' : 'text-rule-strong'}
                      />
                      {/* Edge Label on active selection */}
                      {isHighlighted && link.relation_type && link.relation_type !== 'gap_vector' && (
                        <g transform={`translate(${(sourceNode.x + targetNode.x) / 2}, ${(sourceNode.y + targetNode.y) / 2})`}>
                          <rect
                            x="-32"
                            y="-9"
                            width="64"
                            height="18"
                            rx="4"
                            className="fill-paper border border-rule"
                            opacity="0.9"
                          />
                          <text
                            textAnchor="middle"
                            y="3.5"
                            className="fill-ink text-[10px] font-mono tracking-tight font-medium"
                          >
                            {link.relation_type}
                          </text>
                        </g>
                      )}
                    </g>
                  );
                })}
              </g>

              {/* ── 2. Graph Nodes ───────────────────────────────────────── */}
              <g className="nodes-layer">
                {nodes.map((node) => {
                  const isSelected = selectedNodeId === node.id;
                  const isConnected = !selectedNodeId || connectedNodeIds.has(node.id);
                  const isMatch = searchMatches.size > 0 && searchMatches.has(node.id);

                  const opacity = isConnected ? 1.0 : 0.14;

                  return (
                    <g
                      key={node.id}
                      transform={`translate(${node.x}, ${node.y})`}
                      opacity={opacity}
                      onClick={(e) => handleNodeClick(node, e)}
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        setDraggedNodeId(node.id);
                      }}
                      className="cursor-pointer transition-opacity duration-200"
                    >
                      {/* Active Selection Glow Ring */}
                      {isSelected && (
                        <circle
                          r={node.radius + 8}
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeDasharray="4 3"
                          className="text-ink animate-spin"
                          style={{ animationDuration: '14s' }}
                        />
                      )}

                      {/* Matching search highlight */}
                      {isMatch && (
                        <circle
                          r={node.radius + 6}
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          className="text-ink animate-pulse"
                        />
                      )}

                      {/* Node Body Seal */}
                      {node.isFrontier ? (
                        /* Frontier / Study Next Node */
                        <g>
                          <circle
                            r={node.radius}
                            className="fill-paper-raised stroke-ink"
                            strokeWidth="2"
                            strokeDasharray="4 3"
                            filter="url(#carto-shadow)"
                          />
                          {/* Inner breathing frontier pulse */}
                          <circle
                            r={node.radius - 6}
                            className="fill-paper-inset opacity-80"
                          />
                          {/* Frontier Sparkle Glyph */}
                          <text
                            textAnchor="middle"
                            y="4.5"
                            className="fill-ink text-[13px] font-bold"
                          >
                            ✦
                          </text>
                        </g>
                      ) : (
                        /* Grounded Concept / Technology / Metric Node */
                        <g filter="url(#carto-shadow)">
                          {node.type.toLowerCase() === 'technology' ? (
                            // Double Ring for Technology
                            <g>
                              <circle r={node.radius} className="fill-paper-raised stroke-ink" strokeWidth="2" />
                              <circle r={node.radius - 5} className="fill-none stroke-ink-muted" strokeWidth="1.2" strokeDasharray="3 2" />
                            </g>
                          ) : node.type.toLowerCase() === 'metric' ? (
                            // Beveled Square for Metrics
                            <rect
                              x={-node.radius}
                              y={-node.radius}
                              width={node.radius * 2}
                              height={node.radius * 2}
                              rx="5"
                              className="fill-paper-raised stroke-ink"
                              strokeWidth="2"
                            />
                          ) : node.type.toLowerCase() === 'organization' ? (
                            // Rotated Diamond for Organizations
                            <rect
                              x={-node.radius * 0.9}
                              y={-node.radius * 0.9}
                              width={node.radius * 1.8}
                              height={node.radius * 1.8}
                              rx="3"
                              transform="rotate(45)"
                              className="fill-paper-raised stroke-ink"
                              strokeWidth="2"
                            />
                          ) : (
                            // Concept: Deep Solid Seal
                            <circle
                              r={node.radius}
                              className="fill-paper-raised stroke-ink"
                              strokeWidth={isSelected ? '2.5' : '1.8'}
                            />
                          )}

                          {/* Inner core glyph / marker */}
                          <circle
                            r={Math.max(4, node.radius * 0.3)}
                            className="fill-ink"
                          />
                        </g>
                      )}

                      {/* Node Label Card */}
                      <g transform={`translate(0, ${node.radius + 14})`}>
                        <text
                          textAnchor="middle"
                          className={`text-[11px] font-sans tracking-tight fill-ink ${
                            isSelected ? 'font-bold' : node.isFrontier ? 'font-semibold italic' : 'font-medium'
                          }`}
                        >
                          {node.name.length > 24 ? `${node.name.slice(0, 22)}…` : node.name}
                        </text>
                        {/* Secondary Type / Priority pill */}
                        <text
                          textAnchor="middle"
                          y="13"
                          className="text-[9px] font-mono uppercase tracking-wider fill-ink-faint"
                        >
                          {node.isFrontier ? `Gap · P${node.priority ?? 1}` : node.type}
                        </text>
                      </g>
                    </g>
                  );
                })}
              </g>
            </g>
          </svg>
        )}
      </div>

      {/* ── Bottom-Left Legend ──────────────────────────────────────────────── */}
      <div className="absolute bottom-4 left-4 z-20 pointer-events-none hidden md:flex items-center gap-4 bg-paper-raised/85 header-gradient border border-rule card-beveled px-3 py-1.5 shadow-sm text-micro text-ink-faint">
        <span className="font-semibold text-ink uppercase tracking-wider text-[10px]">Taxonomy:</span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full border border-ink bg-paper" /> Concept
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full border border-ink border-dashed" /> Technology
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 border border-ink bg-paper" /> Metric
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 border border-ink rotate-45 bg-paper" /> Organization
        </span>
        <span className="flex items-center gap-1.5 font-medium text-ink">
          <span className="text-xs">✦</span> Study Next (Gap)
        </span>
      </div>

      {/* ── Right Inspector / Frontier Detail Slide-over ────────────────────── */}
      {selectedNode && (
        <aside
          className="absolute top-0 right-0 bottom-0 w-84 sm:w-96 bg-paper-raised paper-wash border-l border-rule shadow-drawer z-30 flex flex-col animate-settle"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="px-5 py-4 border-b border-rule flex items-start justify-between gap-3">
            <div className="min-w-0">
              <span className={`inline-flex items-center gap-1 text-micro font-mono uppercase tracking-widest px-2 py-0.5 rounded-sm border ${
                selectedNode.isFrontier
                  ? 'border-rule-strong bg-paper-inset text-ink font-semibold'
                  : 'border-rule text-ink-faint'
              }`}>
                {selectedNode.isFrontier ? '✦ Research Frontier' : selectedNode.type}
              </span>
              <h2 className="font-display text-display-sm text-ink font-semibold mt-1.5 truncate">
                {selectedNode.name}
              </h2>
            </div>
            <button
              type="button"
              onClick={() => setSelectedNodeId(null)}
              className="p-1 text-ink-faint hover:text-ink rounded transition-colors"
              aria-label="Close details"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Body Content */}
          <div className="flex-1 overflow-y-auto px-5 py-5 space-y-6">
            {/* Description / Summary */}
            <div>
              <h3 className="text-micro uppercase tracking-wider text-ink-faint font-semibold mb-2">
                {selectedNode.isFrontier ? 'Research Problem & Gap' : 'Concept Definition'}
              </h3>
              <p className="text-label text-ink leading-relaxed bg-paper/60 p-3 rounded-md border border-rule/80 intaglio-well">
                {selectedNode.summary || 'No summary extracted for this item.'}
              </p>
            </div>

            {/* Killer Feature for Frontier Nodes: Prerequisite Knowledge + 1-Click Research Trigger */}
            {selectedNode.isFrontier ? (
              <div className="space-y-4">
                {/* Prerequisites checklist */}
                <div>
                  <h3 className="text-micro uppercase tracking-wider text-ink-faint font-semibold mb-2 flex items-center gap-1.5">
                    <CheckCircle2 className="w-3.5 h-3.5 text-ink" />
                    <span>Prerequisites to Understand First:</span>
                  </h3>
                  <div className="space-y-1.5">
                    {Array.from(connectedNodeIds)
                      .filter((id) => id !== selectedNode.id)
                      .map((pId) => {
                        const prereqNode = nodes.find((n) => n.id === pId);
                        if (!prereqNode) return null;
                        return (
                          <button
                            key={pId}
                            type="button"
                            onClick={() => setSelectedNodeId(pId)}
                            className="w-full text-left flex items-center justify-between p-2 rounded-md bg-paper border border-rule hover:border-rule-strong hover:bg-paper-raised text-label text-ink transition-all tactile-btn"
                          >
                            <span className="truncate">{prereqNode.name}</span>
                            <span className="text-micro font-mono text-ink-faint capitalize shrink-0 ml-2">
                              {prereqNode.type}
                            </span>
                          </button>
                        );
                      })}
                  </div>
                </div>

                {/* 1-Click Launch Research Run */}
                <div className="pt-2 border-t border-rule">
                  <button
                    type="button"
                    onClick={() => handleLaunchFrontierResearch(selectedNode.targetQuery || selectedNode.name)}
                    className="w-full text-paper-raised font-medium py-3 px-4 rounded-lg flex items-center justify-center gap-2 transition-all bg-ink hover:bg-ink-muted border border-ink shadow-sm tactile-btn"
                  >
                    <Compass className="w-4 h-4" />
                    <span>{copiedQuery ? 'Query Prepared!' : 'Launch Research Run on this Gap →'}</span>
                  </button>
                  <p className="text-micro text-ink-faint text-center mt-2">
                    Transfers question directly to composer and opens run workbench.
                  </p>
                </div>
              </div>
            ) : (
              /* Connected Network Relations for Grounded Nodes */
              <div>
                <h3 className="text-micro uppercase tracking-wider text-ink-faint font-semibold mb-2 flex items-center gap-1.5">
                  <Layers className="w-3.5 h-3.5 text-ink" />
                  <span>Direct Relationships ({connectedNodeIds.size - 1}):</span>
                </h3>
                <div className="space-y-1.5">
                  {Array.from(connectedNodeIds)
                    .filter((id) => id !== selectedNode.id)
                    .map((cId) => {
                      const otherNode = nodes.find((n) => n.id === cId);
                      if (!otherNode) return null;
                      return (
                        <button
                          key={cId}
                          type="button"
                          onClick={() => setSelectedNodeId(cId)}
                          className="w-full text-left flex items-center justify-between p-2 rounded-md bg-paper border border-rule hover:border-rule-strong hover:bg-paper-raised text-label text-ink transition-all tactile-btn"
                        >
                          <span className="truncate">{otherNode.name}</span>
                          <span className="text-micro font-mono text-ink-faint capitalize shrink-0 ml-2">
                            {otherNode.isFrontier ? '✦ Frontier' : otherNode.type}
                          </span>
                        </button>
                      );
                    })}
                </div>
              </div>
            )}
          </div>
        </aside>
      )}
    </div>
  );
};

export default KnowledgeGraphExplorer;
