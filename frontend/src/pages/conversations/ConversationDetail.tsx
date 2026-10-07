import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, downloadFile } from "../../api/client";
import { customerContact, customerLabel, type ConversationDetail as TConversation, type SupportCall } from "../../api/types";
import { IconDownload } from "../../components/Icons";
import { Badge, ErrorBox, Loading, PageHeader, fmtDate, fmtDuration } from "../../components/ui";

const OUTCOME_TEXT: Record<string, string> = {
  answered: "The AI answered the caller from the knowledge base.",
  callback_booked: "The caller asked to be called back; the call is booked under Callbacks.",
  ticket_raised: "A ticket was raised for the team to call back.",
  ticket_updated: "The caller added details to a ticket they already had.",
  abandoned: "The caller hung up before saying what they needed.",
};
const LANGUAGE: Record<string, string> = { "en-IN": "English", "hi-IN": "Hindi" };

function CallSummary({ call, conversationId }: { call: SupportCall; conversationId: string }) {
  const live = call.status === "in_progress";
  return (
    <div className="card">
      <div className="row spread">
        <h2 style={{ margin: 0 }}>Phone call</h2>
        {live ? <Badge value="in_progress" /> : call.outcome && <Badge value={call.outcome} />}
      </div>
      <p className="muted small">{live ? "The caller is on the line with the AI agent now." : call.outcome ? OUTCOME_TEXT[call.outcome] : ""}</p>
      <dl className="meta-list">
        <dt>Caller</dt><dd>{call.from_number ?? "Hidden number"}</dd>
        <dt>Started</dt><dd>{fmtDate(call.started_at)}</dd>
        <dt>Duration</dt><dd>{fmtDuration(call.duration_seconds)}</dd>
        <dt>Language at the end</dt><dd>{call.language ? LANGUAGE[call.language] ?? call.language : "—"}</dd>
      </dl>
      {call.has_recording && (
        <div style={{ marginTop: 14 }}>
          <button className="btn ghost sm" onClick={() => downloadFile(`/conversations/${conversationId}/recording`, `call-${call.id}.mp3`)}>
            <IconDownload size={14} /> Download recording
          </button>
        </div>
      )}
    </div>
  );
}

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
  const phone = data.channel === "phone";
  const email = data.channel === "email";

  return (
    <>
      <PageHeader
        title={<>{customerLabel(data.customer)} <Badge value={data.channel} /> <Badge value={data.status} /></>}
        subtitle={<>{customerContact(data.customer)} · Domain: {data.domain?.name ?? "undetected"} · Started {fmtDate(data.created_at)}</>}
        actions={!phone && data.status !== "closed" && (
          <button className="btn ghost" onClick={() => close.mutate()} disabled={close.isPending}>Close conversation</button>
        )}
      />
      {data.call && <CallSummary call={data.call} conversationId={data.id} />}
      {data.ticket_ids.length > 0 && (
        <div className="card row">
          <strong>{phone && data.call?.outcome === "ticket_updated" ? "Added to ticket:" : "Escalated to ticket:"}</strong>
          {data.ticket_ids.map((t) => <Link key={t} to={`/tickets/${t}`}>View ticket</Link>)}
        </div>
      )}
      <div className="card">
        {phone && <h2>Transcript</h2>}
        <div className="chat">
          {data.messages.map((m) => (
            <div key={m.id} className={`bubble ${m.direction} ${m.role === "agent" ? "agent" : ""}`}>
              {m.content}
              <div className="meta">
                {phone ? (m.role === "customer" ? "caller" : "AI agent") : m.role} · {fmtDate(m.created_at)}
                {m.delivery_status && ` · ${m.delivery_status.replace("_", " ")}`}
                {typeof m.meta.send_error === "string" && ` · ${m.meta.send_error}`}
                {typeof m.meta.rag_status === "string" && ` · RAG: ${m.meta.rag_status}`}
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>
      </div>
      {phone ? (
        <p className="muted small">This was a phone call, so there is nothing to reply to here. Follow up from the ticket, or call the customer back.</p>
      ) : data.status !== "closed" && (
        <form className="card form" onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
          <ErrorBox error={send.error} />
          <textarea placeholder={email ? "Reply to the customer by email as a human agent…" : "Reply to the customer on WhatsApp as a human agent…"}
            value={text} onChange={(e) => setText(e.target.value)} style={email ? { minHeight: 140 } : undefined} />
          <div><button className="btn" disabled={!text.trim() || send.isPending}>{email ? "Send email" : "Send on WhatsApp"}</button></div>
          {email && <span className="hint">Sent from your support address, in the customer's existing email thread. Your company name is added as the signature.</span>}
        </form>
      )}
    </>
  );
}
