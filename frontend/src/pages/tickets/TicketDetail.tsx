import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { ConversationDetail, TicketDetail as TTicket, TicketEvent } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { useAgents } from "../../hooks";
import { Badge, ErrorBox, Loading, PageHeader, fmtDate } from "../../components/ui";

function describe(e: TicketEvent): string {
  const d = e.data as Record<string, string | null>;
  switch (e.event_type) {
    case "created": return `Ticket created from ${d.source}`;
    case "assigned": return d.to_agent_id ? `Assigned${d.auto ? " automatically" : ""}` : "Unassigned";
    case "status_changed": return `Status: ${d.from} → ${d.to}`;
    case "priority_changed": return `Priority: ${d.from} → ${d.to}`;
    case "customer_message": return `${d.channel === "phone" ? "Customer said on a call" : "Customer wrote"}: “${d.text}”`;
    case "comment": return d.text ?? "";
    default: return e.event_type;
  }
}

export default function TicketDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const { user, hasRole } = useAuth();
  const manager = hasRole("admin", "supervisor");
  const { data: agents } = useAgents(manager);
  const [comment, setComment] = useState("");

  const { data: ticket, isLoading, error } = useQuery({
    queryKey: ["ticket", id],
    queryFn: async () => (await api.get<TTicket>(`/tickets/${id}`)).data,
  });
  const { data: conversation } = useQuery({
    queryKey: ["conversation", ticket?.conversation_id],
    queryFn: async () => (await api.get<ConversationDetail>(`/conversations/${ticket!.conversation_id}`)).data,
    enabled: !!ticket?.conversation_id,
  });

  const mutate = useMutation({
    mutationFn: async (req: { url: string; body: unknown; method?: "post" | "patch" }) =>
      (await api[req.method ?? "post"]<TTicket>(req.url, req.body)).data,
    onSuccess: (data) => {
      qc.setQueryData(["ticket", id], data);
      qc.invalidateQueries({ queryKey: ["tickets"] });
    },
  });

  if (isLoading) return <Loading />;
  if (error || !ticket) return <ErrorBox error={error} />;

  return (
    <>
      <PageHeader
        title={<>Ticket #{ticket.number} <Badge value={ticket.status} /> <Badge value={ticket.priority} /></>}
        subtitle={ticket.issue}
      />
      <ErrorBox error={mutate.error} />
      <div className="grid grid-2">
        <div>
          <div className="card">
            <h2>AI summary</h2>
            <pre>{ticket.ai_summary ?? "—"}</pre>
          </div>

          <div className="card">
            <h2>{conversation?.channel === "phone" ? "Call transcript" : "Conversation"}</h2>
            {!conversation ? <p className="muted">No linked conversation.</p> : (
              <>
                <div className="chat">
                  {conversation.messages.map((m) => (
                    <div key={m.id} className={`bubble ${m.direction} ${m.role === "agent" ? "agent" : ""}`}>
                      {m.content}
                      <div className="meta">{m.role} · {fmtDate(m.created_at)}</div>
                    </div>
                  ))}
                </div>
                <p>
                  <Link to={`/conversations/${conversation.id}`}>
                    {conversation.channel === "phone" ? "Open the call: recording and details →" : "Open conversation to reply on WhatsApp →"}
                  </Link>
                </p>
              </>
            )}
          </div>
        </div>

        <div>
          <div className="card form">
            <h2>Details</h2>
            <div className="small muted">Customer</div>
            <div>{ticket.customer.name ?? "—"} · {ticket.customer.phone}</div>
            <div className="small muted">Domain · Source</div>
            <div>{ticket.domain?.name ?? "—"} · {ticket.source}</div>

            <label>
              Status
              <select value={ticket.status} disabled={mutate.isPending}
                onChange={(e) => mutate.mutate({ url: `/tickets/${id}`, method: "patch", body: { status: e.target.value } })}>
                {["open", "in_progress", "pending_customer", "resolved", "closed"].map((s) => (
                  <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
                ))}
              </select>
            </label>
            <label>
              Priority
              <select value={ticket.priority} disabled={mutate.isPending}
                onChange={(e) => mutate.mutate({ url: `/tickets/${id}`, method: "patch", body: { priority: e.target.value } })}>
                {["low", "medium", "high", "urgent"].map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label>
              Assignee
              {manager ? (
                <select value={ticket.assigned_to?.id ?? ""} disabled={mutate.isPending}
                  onChange={(e) => mutate.mutate({ url: `/tickets/${id}/assign`, body: { agent_id: e.target.value || null } })}>
                  <option value="">Unassigned</option>
                  {agents?.map((a) => <option key={a.id} value={a.id}>{a.full_name}</option>)}
                </select>
              ) : (
                <div className="row">
                  <span>{ticket.assigned_to?.full_name ?? "Unassigned"}</span>
                  {!ticket.assigned_to && (
                    <button className="btn sm" onClick={() => mutate.mutate({ url: `/tickets/${id}/assign`, body: { agent_id: user!.id } })}>
                      Take it
                    </button>
                  )}
                </div>
              )}
            </label>
          </div>

          <div className="card">
            <h2>Activity</h2>
            <ul className="timeline">
              {ticket.events.map((e) => (
                <li key={e.id}>
                  <div>{describe(e)}</div>
                  <div className="small muted">{e.actor?.full_name ?? "System"} · {fmtDate(e.created_at)}</div>
                </li>
              ))}
            </ul>
            <form className="form" style={{ marginTop: 12 }}
              onSubmit={(e) => {
                e.preventDefault();
                mutate.mutate({ url: `/tickets/${id}/comments`, body: { text: comment } }, { onSuccess: () => setComment("") });
              }}>
              <textarea placeholder="Add an internal note…" value={comment} onChange={(e) => setComment(e.target.value)} />
              <button className="btn" disabled={!comment.trim() || mutate.isPending}>Add note</button>
            </form>
          </div>
        </div>
      </div>
    </>
  );
}
