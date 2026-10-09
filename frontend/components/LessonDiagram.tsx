"use client";

import { useId } from "react";
import type { components } from "@/lib/generated/api";

type Statement = components["schemas"]["PreparedStatementOut"];
type Edge = components["schemas"]["PreparedDiagramEdgeOut"];

export function LessonDiagram({ nodes, edges, onOpenSource }: {
  nodes: Statement[]; edges: Edge[]; onOpenSource: (id: string) => void;
}) {
  const markerId = useId().replaceAll(":", "");
  const positions = nodes.reduce<{ y: number; height: number; center: number }[]>((layout, node) => {
    const height = Math.max(110, Math.ceil(node.text.length / 35) * 24 + 58);
    const previous = layout.at(-1);
    const y = previous ? previous.y + previous.height + 24 : 20;
    return [...layout, { y, height, center: y + height / 2 }];
  }, []);
  const last = positions.at(-1);
  const nextY = last ? last.y + last.height + 24 : 40;
  const validEdges = edges.filter((edge) => positions[edge.from_index] && positions[edge.to_index]
    && edge.from_index !== edge.to_index);

  return <figure className="my-5" aria-label="Source-grounded diagram">
    <figcaption className="mb-3"><h2 className="text-xl font-bold">Connections in your course material</h2>
      <p className="mt-2 text-sm text-zinc-700">Arrows show source-checked relationships. Read each connection and its supporting passage below.</p></figcaption>
    {validEdges.length > 0 ? <div className="max-h-[42rem] overflow-auto border-2 border-black bg-zinc-50">
      <svg viewBox={`0 0 720 ${nextY}`} className="min-w-[600px] w-full" role="img" aria-label="Directed connections between lesson points">
        <title>Source-grounded lesson points and directed connections</title>
        <defs><marker id={markerId} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
        {validEdges.map((edge, index) => {
          const start = positions[edge.from_index].center;
          const end = positions[edge.to_index].center;
          const gutter = 24 + index * 7;
          return <g key={index}><path d={`M 150 ${start} H ${gutter} V ${end} H 150`} fill="none" stroke="currentColor" strokeWidth="2" markerEnd={`url(#${markerId})`} />
            <text x={gutter + 5} y={(start + end) / 2} fontSize="12" fontWeight="bold">{index + 1}</text></g>;
        })}
        {nodes.map((node, index) => <g key={index}>
          <rect x="150" y={positions[index].y} width="550" height={positions[index].height} fill="white" stroke="black" strokeWidth="2" />
          <foreignObject x="165" y={positions[index].y + 12} width="520" height={positions[index].height - 20}>
            <div className="text-sm leading-6 [overflow-wrap:anywhere]"><strong>Point {index + 1}</strong><p>{node.text}</p></div>
          </foreignObject>
        </g>)}
      </svg>
    </div> : <p role="status" className="border-2 border-amber-800 bg-amber-50 p-3">This saved variant has no validated connections. Choose another format while a connected diagram is prepared.</p>}
    <ol className="mt-4 space-y-4" aria-label="Diagram connections">
      {validEdges.map((edge, index) => <li key={index} className="border-l-4 border-[#FFD23F] pl-3">
        <h3 className="font-bold">Connection {index + 1}: Point {edge.from_index + 1} → Point {edge.to_index + 1}</h3>
        <p className="mt-1 leading-relaxed">{edge.text}</p>
        <div className="mt-2 flex flex-wrap gap-2">{edge.citation_chunk_ids.map((id, sourceIndex) => <button type="button" key={id}
          className="nl-chip" onClick={() => onOpenSource(id)}>View connection source {sourceIndex + 1}</button>)}</div>
      </li>)}
    </ol>
  </figure>;
}
