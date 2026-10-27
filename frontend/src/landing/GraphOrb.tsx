import React, { useEffect, useRef } from 'react';

/**
 * A slowly turning knowledge graph, drawn in ink.
 *
 * Nodes sit on a sphere and are drawn as the product's status marks (filled,
 * half, empty, dotted squares), linked to their nearest neighbours by
 * hairlines. Depth is carried by ink weight only: near nodes are dark and
 * large, far ones faint and small. Drag to turn it.
 */

type Kind = 'filled' | 'half' | 'empty' | 'dotted';
interface Node { x: number; y: number; z: number; kind: Kind }

const KINDS: Kind[] = ['filled', 'filled', 'filled', 'half', 'empty', 'dotted'];

function buildGraph(count: number): { nodes: Node[]; edges: [number, number][] } {
  const nodes: Node[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const t = golden * i;
    const jitter = 0.82 + ((i * 37) % 17) / 17 * 0.3;
    nodes.push({ x: Math.cos(t) * r * jitter, y: y * jitter, z: Math.sin(t) * r * jitter, kind: KINDS[(i * 7) % KINDS.length] });
  }
  const edges: [number, number][] = [];
  const seen = new Set<string>();
  nodes.forEach((a, i) => {
    nodes
      .map((b, j) => ({ j, d: (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2 }))
      .filter((o) => o.j !== i)
      .sort((p, q) => p.d - q.d)
      .slice(0, 2 + (i % 2))
      .forEach(({ j }) => {
        const key = i < j ? `${i}-${j}` : `${j}-${i}`;
        if (!seen.has(key)) { seen.add(key); edges.push([i, j]); }
      });
  });
  return { nodes, edges };
}

export const GraphOrb: React.FC<{ className?: string }> = ({ className = '' }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const { nodes, edges } = buildGraph(54);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let rotY = 0.6;
    let rotX = -0.25;
    let velocity = reduced ? 0 : 0.0022;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let visible = true;
    let frame = 0;

    const inkRgb = () => getComputedStyle(document.documentElement).getPropertyValue('--ink-rgb').trim() || '19, 17, 13';
    const paperRgb = () => getComputedStyle(document.documentElement).getPropertyValue('--paper-raised-rgb').trim() || '242, 237, 227';

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const { width, height } = canvas.getBoundingClientRect();
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = () => {
      const { width, height } = canvas.getBoundingClientRect();
      const ink = inkRgb();
      const paper = paperRgb();
      const scale = Math.min(width, height) * 0.38;
      const cx = width / 2;
      const cy = height / 2;
      const sy = Math.sin(rotY), cyy = Math.cos(rotY);
      const sx = Math.sin(rotX), cxx = Math.cos(rotX);

      const projected = nodes.map((n) => {
        const x1 = n.x * cyy - n.z * sy;
        const z1 = n.x * sy + n.z * cyy;
        const y1 = n.y * cxx - z1 * sx;
        const z2 = n.y * sx + z1 * cxx;
        const p = 2.6 / (2.6 + z2);
        return { sx: cx + x1 * scale * p, sy: cy + y1 * scale * p, depth: (1 - z2) / 2, p, kind: n.kind };
      });

      ctx.clearRect(0, 0, width, height);

      ctx.lineWidth = 1;
      for (const [a, b] of edges) {
        const pa = projected[a], pb = projected[b];
        const alpha = 0.08 + 0.32 * Math.min(pa.depth, pb.depth);
        ctx.strokeStyle = `rgba(${ink}, ${alpha.toFixed(3)})`;
        ctx.beginPath();
        ctx.moveTo(pa.sx, pa.sy);
        ctx.lineTo(pb.sx, pb.sy);
        ctx.stroke();
      }

      const order = projected.map((_, i) => i).sort((i, j) => projected[i].depth - projected[j].depth);
      for (const i of order) {
        const n = projected[i];
        const size = 3 + 6 * n.p * n.depth + 2;
        const alpha = 0.25 + 0.75 * n.depth;
        const x = n.sx - size / 2;
        const y = n.sy - size / 2;
        // Paper behind each mark so a near node occludes the lines behind it.
        ctx.fillStyle = `rgb(${paper})`;
        ctx.fillRect(x, y, size, size);
        ctx.strokeStyle = `rgba(${ink}, ${alpha.toFixed(3)})`;
        ctx.fillStyle = `rgba(${ink}, ${alpha.toFixed(3)})`;
        if (n.kind === 'filled') {
          ctx.fillRect(x, y, size, size);
        } else if (n.kind === 'half') {
          ctx.fillRect(x, y + size / 2, size, size / 2);
          ctx.strokeRect(x + 0.5, y + 0.5, size - 1, size - 1);
        } else if (n.kind === 'empty') {
          ctx.strokeRect(x + 0.5, y + 0.5, size - 1, size - 1);
        } else {
          ctx.setLineDash([1.5, 1.5]);
          ctx.strokeRect(x + 0.5, y + 0.5, size - 1, size - 1);
          ctx.setLineDash([]);
        }
      }
    };

    const tick = () => {
      if (!dragging) {
        rotY += velocity;
        // Ease any drag flick back to the resting drift.
        const rest = reduced ? 0 : 0.0022;
        velocity += (rest - velocity) * 0.02;
      }
      draw();
      if (visible && (!reduced || dragging)) frame = requestAnimationFrame(tick);
    };

    const onDown = (e: PointerEvent) => {
      dragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
      canvas.setPointerCapture(e.pointerId);
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(tick);
    };
    const onMove = (e: PointerEvent) => {
      if (!dragging) return;
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      rotY += dx * 0.008;
      rotX = Math.max(-1.2, Math.min(1.2, rotX + dy * 0.008));
      velocity = dx * 0.0012;
    };
    const onUp = () => { dragging = false; };

    resize();
    draw();
    const ro = new ResizeObserver(() => { resize(); draw(); });
    ro.observe(canvas);
    const io = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      cancelAnimationFrame(frame);
      if (visible && !reduced) frame = requestAnimationFrame(tick);
    });
    io.observe(canvas);
    // Redraw when the theme flips, so the ink follows the page.
    const mo = new MutationObserver(draw);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('pointercancel', onUp);

    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      io.disconnect();
      mo.disconnect();
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('pointercancel', onUp);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className={`block w-full h-full cursor-grab active:cursor-grabbing touch-none ${className}`}
      role="img"
      aria-label="A turning knowledge graph whose nodes are drawn as claim status marks"
    />
  );
};
