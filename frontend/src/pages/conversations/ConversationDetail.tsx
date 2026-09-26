import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { ConversationDetail as TConversation } from "../../api/types";
import { Badge, ErrorBox, Loading, PageHeader, fmtDate } from "../../components/ui";

export default function ConversationDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["conversation", id],
    queryFn: async () => (await api.get<TConversation>(`/conversations/${id}`)).data,
    refetchInterval: 5_000,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["conversation", id] });

  const send = useMutation({
    mutationFn: async () => api.post(`/conversations/${id}/messages`, { text }),
    onSuccess: () => {
      setText("");
      refresh();
    },
  });
  const close = useMutation({ mutationFn: async () => api.post(`/conversations/${id}/close`), onSuccess: refresh });

  const count = data?.messages.length;
  useEffect(() => bottom.current?.scrollIntoView({ block: "end" }), [count]);

  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;

  return (
    <>
      <PageHeader
        title={<>{data.customer.name ?? data.customer.phone} <Badge value={data.status} /></>}
        subtitle={<>{data.customer.phone} · Domain: {data.domain?.name ?? "undetected"} · Started {fmtDate(data.created_at)}</>}
        actions={data.status !== "closed" && (
          <button className="btn ghost" onClick={() => close.mutate()} disabled={close.isPending}>Close conversation</button>
        )}
      />
      {data.ticket_ids.length > 0 && (
        <div className="card row">
          <strong>Escalated to ticket:</strong>
          {data.ticket_ids.map((t) => <Link key={t} to={`/tickets/${t}`}>View ticket</Link>)}
        </div>
      )}
      <div className="card">
        <div className="chat">
          {data.messages.map((m) => (
            <div key={m.id} className={`bubble ${m.direction} ${m.role === "agent" ? "agent" : ""}`}>
              {m.content}
              <div className="meta">
                {m.role} · {fmtDate(m.created_at)}
                {m.delivery_status && ` · ${m.delivery_status}`}
                {typeof m.meta.rag_status === "string" && ` · RAG: ${m.meta.rag_status}`}
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>
      </div>
      {data.status !== "closed" && (
        <form className="card form" onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
          <ErrorBox error={send.error} />
          <textarea placeholder="Reply to the customer on WhatsApp as a human agent…" value={text} onChange={(e) => setText(e.target.value)} />
          <div><button className="btn" disabled={!text.trim() || send.isPending}>Send on WhatsApp</button></div>
        </form>
      )}
    </>
  );
}
