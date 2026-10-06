import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { Campaign, Page } from "../../api/types";
import { cleanParams, useCompany } from "../../hooks";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Pagination, fmtDate } from "../../components/ui";

export default function CampaignList() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const { data, isLoading, error } = useQuery({
    queryKey: ["campaigns", page, status],
    queryFn: async () => (await api.get<Page<Campaign>>("/campaigns", { params: cleanParams({ page, status }) })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
  const usage = useCompany().data?.usage;
  const limitReached = !!usage && usage.max_campaigns !== null && usage.campaigns >= usage.max_campaigns;

  return (
    <>
      <PageHeader
        title="Outbound Campaigns"
        subtitle="Excel → Campaign → Schedule → AI voice calls → structured answers"
        actions={
          <>
            {usage && usage.max_campaigns !== null && (
              <span className="pill" title="Set by the platform administrator">{usage.campaigns} of {usage.max_campaigns} campaigns used</span>
            )}
            {usage && usage.daily_call_limit !== null && (
              <span className="pill" title="Set by the platform administrator">{usage.calls_today} of {usage.daily_call_limit} calls today</span>
            )}
            {limitReached
              ? <button className="btn" disabled title="Campaign limit reached">+ New campaign</button>
              : <Link className="btn" to="/campaigns/new">+ New campaign</Link>}
          </>
        }
      />
      <div className="filters">
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All statuses</option>
          {["draft", "scheduled", "running", "paused", "completed", "cancelled"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      <ErrorBox error={error} />
      {isLoading ? <Loading /> : !data?.items.length ? <Empty>No campaigns yet.</Empty> : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Status</th><th>Questions</th><th>Progress</th><th>Start</th><th>Created</th></tr></thead>
              <tbody>
                {data.items.map((c) => {
                  const total = c.contact_count ?? 0;
                  const done = c.completed_count ?? 0;
                  return (
                    <tr key={c.id} className="clickable" onClick={() => navigate(`/campaigns/${c.id}`)}>
                      <td>{c.name}</td>
                      <td><Badge value={c.status} /></td>
                      <td>{c.questions.length}</td>
                      <td style={{ minWidth: 160 }}>
                        <div className="small">{done} / {total} completed</div>
                        <div className="bar"><div style={{ width: `${total ? (100 * done) / total : 0}%` }} /></div>
                      </td>
                      <td className="small">{fmtDate(c.start_at)}</td>
                      <td className="small">{fmtDate(c.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}
