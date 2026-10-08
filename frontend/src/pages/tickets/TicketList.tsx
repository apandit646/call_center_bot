import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { customerContact } from "../../api/types";
import type { Page, Ticket } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { cleanParams, useDomains } from "../../hooks";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Pagination, fmtDate } from "../../components/ui";
import { Select } from "../../components/Select";

export default function TicketList() {
  const navigate = useNavigate();
  const { hasRole } = useAuth();
  const { data: domains } = useDomains();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ q: "", status: "", priority: "", domain_id: "", mine: false, unassigned: false });
  const set = (patch: Partial<typeof filters>) => {
    setFilters((f) => ({ ...f, ...patch }));
    setPage(1);
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ["tickets", page, filters],
    queryFn: async () =>
      (await api.get<Page<Ticket>>("/tickets", { params: cleanParams({ ...filters, page, size: 25 }) })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 20_000,
  });

  return (
    <>
      <PageHeader title="Tickets" subtitle="Issues the WhatsApp assistant could not resolve" />
      <div className="filters">
        <input placeholder="Search #number, issue, customer…" value={filters.q} onChange={(e) => set({ q: e.target.value })} />
        <Select value={filters.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="">All statuses</option>
          {["open", "in_progress", "pending_customer", "resolved", "closed"].map((s) => (
            <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
          ))}
        </Select>
        <Select value={filters.priority} onChange={(e) => set({ priority: e.target.value })}>
          <option value="">All priorities</option>
          {["urgent", "high", "medium", "low"].map((p) => <option key={p} value={p}>{p}</option>)}
        </Select>
        <Select value={filters.domain_id} onChange={(e) => set({ domain_id: e.target.value })}>
          <option value="">All domains</option>
          {domains?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </Select>
        {hasRole("admin", "supervisor") && (
          <label className="checkbox"><input type="checkbox" checked={filters.mine} onChange={(e) => set({ mine: e.target.checked })} /> Mine</label>
        )}
        <label className="checkbox"><input type="checkbox" checked={filters.unassigned} onChange={(e) => set({ unassigned: e.target.checked })} /> Unassigned</label>
      </div>

      <ErrorBox error={error} />
      {isLoading ? <Loading /> : !data?.items.length ? <Empty>No tickets match these filters.</Empty> : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>#</th><th>Issue</th><th>Customer</th><th>Domain</th><th>Priority</th><th>Status</th><th>Assignee</th><th>Created</th></tr>
              </thead>
              <tbody>
                {data.items.map((t) => (
                  <tr key={t.id} className="clickable" onClick={() => navigate(`/tickets/${t.id}`)}>
                    <td>#{t.number}</td>
                    <td>{t.issue}</td>
                    <td>{t.customer.name ?? "—"}<div className="small muted">{customerContact(t.customer)}</div></td>
                    <td>{t.domain?.name ?? "—"}</td>
                    <td><Badge value={t.priority} /></td>
                    <td><Badge value={t.status} /></td>
                    <td>{t.assigned_to?.full_name ?? <span className="muted">Unassigned</span>}</td>
                    <td className="small">{fmtDate(t.created_at)}</td>
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
