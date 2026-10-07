import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { InboundCall, Page } from "../../api/types";
import { cleanParams, useCompany } from "../../hooks";
import { IconAlert, IconPhone } from "../../components/Icons";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Pagination, fmtDate, fmtDuration } from "../../components/ui";

const LANGUAGE: Record<string, string> = { "en-IN": "English", "hi-IN": "Hindi" };

export default function InboundCalls() {
  const navigate = useNavigate();
  const { data: company } = useCompany();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ q: "", outcome: "" });
  const set = (patch: Partial<typeof filters>) => {
    setFilters((f) => ({ ...f, ...patch }));
    setPage(1);
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ["inbound-calls", page, filters],
    queryFn: async () =>
      (await api.get<Page<InboundCall>>("/inbound-calls", { params: cleanParams({ ...filters, page, size: 25 }) })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });

  return (
    <>
      <PageHeader
        title="Inbound Calls"
        subtitle={`Every call to ${(company?.telephony_provider === "exotel" ? company.exotel_number : company?.twilio_from_number) ?? "your number"} answered by the AI agent: who called, what happened, and the ticket it led to.`}
      />
      {company && !company.inbound_active && (
        <div className="error" role="status">
          <IconAlert size={16} />
          <span>
            <strong>Inbound calls are off, so no calls are being answered or recorded.</strong>{" "}
            {company.inbound_problem ?? "Switch them on under Company Settings → Calls & WhatsApp → Inbound calls."}{" "}
            <Link to="/company">Open Company Settings</Link>
          </span>
        </div>
      )}
      <div className="filters">
        <input placeholder="Search caller name or number…" value={filters.q} onChange={(e) => set({ q: e.target.value })} />
        <select value={filters.outcome} onChange={(e) => set({ outcome: e.target.value })} aria-label="Outcome">
          <option value="">All outcomes</option>
          <option value="answered">Answered by AI</option>
          <option value="ticket_raised">Ticket raised</option>
          <option value="ticket_updated">Ticket updated</option>
          <option value="callback_booked">Callback booked</option>
          <option value="abandoned">Abandoned</option>
        </select>
      </div>
      <ErrorBox error={error} />
      {isLoading ? <Loading /> : !data?.items.length ? (
        <Empty icon={<IconPhone size={28} />}>
          No inbound calls yet. When a customer calls your number, the call appears here with the caller, the transcript and the recording.
        </Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Caller</th><th>Started</th><th>Duration</th><th>Outcome</th><th>Ticket</th><th>Language</th><th>Recording</th></tr></thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => navigate(`/conversations/${c.conversation_id}`)}>
                    <td>
                      {c.from_number ? (c.customer.name ?? c.from_number) : "Hidden number"}
                      {c.from_number && c.customer.name && <div className="small muted">{c.from_number}</div>}
                      {c.is_callback && <div style={{ marginTop: 4 }}><Badge value="callback" /></div>}
                    </td>
                    <td className="small">{fmtDate(c.started_at)}</td>
                    <td>{c.status === "in_progress" ? <Badge value="in_progress" /> : fmtDuration(c.duration_seconds)}</td>
                    <td>{c.outcome ? <Badge value={c.outcome} /> : <span className="muted">—</span>}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {c.ticket_id ? <Link to={`/tickets/${c.ticket_id}`}>#{c.ticket_number}</Link> : <span className="muted">—</span>}
                    </td>
                    <td className="small">{c.language ? LANGUAGE[c.language] ?? c.language : "—"}</td>
                    <td className="small">{c.has_recording ? "Yes" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}
