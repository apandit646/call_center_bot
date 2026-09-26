import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { Conversation, Page } from "../../api/types";
import { cleanParams, useDomains } from "../../hooks";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Pagination, fmtDate } from "../../components/ui";

export default function ConversationList() {
  const navigate = useNavigate();
  const { data: domains } = useDomains();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ q: "", status: "", domain_id: "" });
  const set = (patch: Partial<typeof filters>) => {
    setFilters((f) => ({ ...f, ...patch }));
    setPage(1);
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ["conversations", page, filters],
    queryFn: async () =>
      (await api.get<Page<Conversation>>("/conversations", { params: cleanParams({ ...filters, page, size: 25 }) })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });

  return (
    <>
      <PageHeader title="WhatsApp Conversations" subtitle="Every chat handled by the AI assistant" />
      <div className="filters">
        <input placeholder="Search customer name or phone…" value={filters.q} onChange={(e) => set({ q: e.target.value })} />
        <select value={filters.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="">All statuses</option>
          {["open", "resolved", "escalated", "closed"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={filters.domain_id} onChange={(e) => set({ domain_id: e.target.value })}>
          <option value="">All domains</option>
          {domains?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </div>
      <ErrorBox error={error} />
      {isLoading ? <Loading /> : !data?.items.length ? <Empty>No conversations yet.</Empty> : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Customer</th><th>Domain</th><th>Status</th><th>Last message</th><th>Started</th></tr></thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => navigate(`/conversations/${c.id}`)}>
                    <td>{c.customer.name ?? "—"}<div className="small muted">{c.customer.phone}</div></td>
                    <td>{c.domain?.name ?? <span className="muted">Undetected</span>}</td>
                    <td><Badge value={c.status} /></td>
                    <td className="small">{fmtDate(c.last_message_at)}</td>
                    <td className="small">{fmtDate(c.created_at)}</td>
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
