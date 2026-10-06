import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { Company, Page, PlatformSummary } from "../../api/types";
import { cleanParams } from "../../hooks";
import { IconBuilding, IconCheck, IconPhone, IconSparkle, IconUsers } from "../../components/Icons";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Pagination, Stat, fmtDate } from "../../components/ui";

const limit = (used: number, max: number | null) => `${used.toLocaleString()} / ${max === null ? "∞" : max.toLocaleString()}`;

export default function Companies() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");

  const summary = useQuery({
    queryKey: ["platform-summary"],
    queryFn: async () => (await api.get<PlatformSummary>("/platform/summary")).data,
    refetchInterval: 30_000,
  });
  const { data, isLoading, error } = useQuery({
    queryKey: ["companies", page, q],
    queryFn: async () => (await api.get<Page<Company>>("/companies", { params: cleanParams({ page, q }) })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
  const s = summary.data;

  return (
    <>
      <PageHeader
        title="Companies"
        subtitle="Every company has its own portal, customers, knowledge base, phone number and WhatsApp number. You set what each one is allowed to use."
        actions={<Link className="btn" to="/companies/new">+ Register company</Link>}
      />
      {s && (
        <div className="grid grid-4" style={{ marginBottom: 18 }}>
          <Stat label="Companies" value={s.companies} hint={`${s.active_companies} active`} icon={<IconBuilding size={18} />} tone="violet" />
          <Stat label="Company users" value={s.users} icon={<IconUsers size={18} />} tone="blue" />
          <Stat label="Running campaigns" value={s.running_campaigns} hint={`${s.campaigns} in total`} icon={<IconSparkle size={18} />} tone="green" />
          <Stat label="Calls in last 24h" value={s.calls_last_24h} hint={`${s.active_calls} active now`} icon={<IconPhone size={18} />} tone="amber" />
        </div>
      )}
      <div className="filters">
        <input placeholder="Search by name or email…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      </div>
      <ErrorBox error={error || summary.error} />
      {isLoading ? <Loading /> : !data?.items.length ? (
        <Empty icon={<IconBuilding size={28} />}>No companies yet. Register the first one to give it a login.</Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Company</th><th>Status</th><th>Campaigns</th><th>Calls today</th><th>Users</th><th>Calls</th><th>WhatsApp</th><th>Registered</th></tr></thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => navigate(`/companies/${c.id}`)}>
                    <td><strong>{c.name}</strong><div className="small muted">{c.email ?? "no email"}</div></td>
                    <td><Badge value={c.is_active ? "active" : "suspended"} /></td>
                    <td>{c.usage ? limit(c.usage.campaigns, c.usage.max_campaigns) : "—"}</td>
                    <td>{c.usage ? limit(c.usage.calls_today, c.usage.daily_call_limit) : "—"}</td>
                    <td>{c.usage?.users ?? "—"}</td>
                    <td>{c.calls_connected ? <><IconCheck size={16} aria-label="Connected" /> <span className="small">{c.telephony_provider === "exotel" ? "Exotel" : "Twilio"}</span></> : <span className="muted small">Not connected</span>}</td>
                    <td>{c.whatsapp_connected ? <IconCheck size={16} aria-label="Connected" /> : <span className="muted small">Not connected</span>}</td>
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
