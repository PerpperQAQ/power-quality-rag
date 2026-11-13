import type { KnowledgeChunk } from "./types";

/** Render source values as React text, never as executable source HTML. */
export function StructuredTable({table}: {table: NonNullable<KnowledgeChunk["table"]>}) {
  const width=Math.max(table.headers.length,...table.rows.map((r)=>r.length));
  return <div className="parsed-table-scroll"><table className="parsed-table" aria-label="原文结构化表格">
    <thead><tr>{Array.from({length:width},(_,i)=><th key={i} scope="col">{table.headers[i] || `列 ${i+1}`}</th>)}</tr></thead>
    <tbody>{table.rows.map((row,i)=><tr key={i}>{Array.from({length:width},(_,j)=><td key={j}>{row[j] ?? ""}</td>)}</tr>)}</tbody>
  </table></div>;
}
