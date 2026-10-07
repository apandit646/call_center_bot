import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { CampaignCallback, Page } from "../../api/types";
import { cleanParams } from "../../hooks";
import { Badge, Empty, ErrorBox, Loading, Modal, Pagination, fmtDate, type Tone } from "../../components/ui";

const localInput = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
const STATUS: Record<CampaignCallback["status"], { label: string; tone: Tone }> = {
  waiting: { label: "waiting", tone: "blue" }, on_hold: { label: "on hold", tone: "amber" }, overdue: { label: "overdue", tone: "red" },
  calling: { label: "calling", tone: "amber" }, done: { label: "done", tone: "green" }, not_reached: { label: "not reached", tone: "red" },
  cancelled: { label: "cancelled", tone: "grey" },
};

/** Call-backs promised on campaign calls: the campaign places each one at its scheduled time. */
export default function CampaignCallbacks() {
  const qc = useQueryClient();
  const [show, setShow] = useState<"upcoming" | "finished">("upcoming");
  const [when, setWhen] = useState("");
  const [page, setPage] = useState(1);
  const [moving, setMoving] = useState<CampaignCallback | null>(null);
  const [time, setTime] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["campaign-callbacks", show, when, page],
    queryFn: async () => (await api.get<Page<CampaignCallback>>("/callbacks/campaign", { params: cleanParams({ show, when, page }) })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["campaign-callbacks"] });
    qc.invalidateQueries({ queryKey: ["contacts"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const cancel = useMutation({ mutationFn: async (id: string) => api.post(`/callbacks/campaign/${id}/cancel`), onSuccess: refresh });
  const move = useMutation({
    mutationFn: async () => api.patch(`/callbacks/campaign/${moving!.id}`, { scheduled_at: new Date(time).toISOString() }),
    onSuccess: () => { setMoving(null); refresh(); },
  });
  const open = (c: CampaignCallback) => {
    move.reset();
    setMoving(c);
    setTime(localInput(new Date(Math.max(Date.now() + 10 * 60_000, new Date(c.scheduled_at).getTime()))));
  };
  const name = (c: CampaignCallback) => c.customer.name ?? c.customer.phone ?? "this customer";

  return (
    <>
      <div className="filters">
        <div className="tabs" style={{ marginBottom: 0 }}>
          <button className={show === "upcoming" ? "active" : ""} onClick={() => { setShow("upcoming"); setPage(1); }}>To make</button>
          <button className={show === "finished" ? "active" : ""} onClick={() => { setShow("finished"); setPage(1); }}>Made or cancelled</button>
        </div>
        <select value={when} onChange={(e) => { setWhen(e.target.value); setPage(1); }} aria-label="When">
          <option value="">Any day</option>
          <option value="today">Today</option>
          <option value="tomorrow">Tomorrow</option>
          <option value="week">Next 7 days</option>
        </select>
      </div>
      <ErrorBox error={error || cancel.error} />
      {isLoading ? <Loading /> : !data?.items.length ? (
        <Empty>
          {show === "upcoming"
            ? "No campaign call-backs waiting. When a customer on a campaign call says \"call me later\", it appears here with the time."
            : "Nothing here yet."}
        </Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Customer</th><th>Campaign</th><th>Call at</th><th>Time set by</th><th>Status</th><th /></tr></thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id}>
                    <td>{c.customer.name ?? c.customer.phone}{c.customer.name && <div className="small muted">{c.customer.phone}</div>}</td>
                    <td className="cell-wrap">
                      <Link to={`/campaigns/${c.campaign_id}`}>{c.campaign_name}</Link>
                      {c.run != null && c.run > 1 && <div className="small muted">run {c.run}</div>}
                    </td>
                    <td className="nowrap"><strong>{fmtDate(c.scheduled_at)}</strong></td>
                    <td className="small">
                      {c.chosen_by_customer ? "The customer" : "Automatic retry"}
                      {c.times_asked > 1 && <div className="muted">asked {c.times_asked} times</div>}
                    </td>
                    <td className="cell-wrap">
                      <Badge value={STATUS[c.status].label} tone={STATUS[c.status].tone} />
                      {c.reason && <div className="small muted" style={{ marginTop: 6 }}>{c.reason}</div>}
                    </td>
                    <td>
                      <div className="cell-actions">
                        {c.call_id && <Link className="btn ghost sm" to={`/calls/${c.call_id}`}>Open call</Link>}
                        {c.can_edit && <button className="btn ghost sm" onClick={() => open(c)}>Change time</button>}
                        {c.can_edit && (
                          <button className="btn danger sm" disabled={cancel.isPending}
                            onClick={() => confirm(`Cancel the call-back to ${name(c)}? They will not be called again in this run.`) && cancel.mutate(c.id)}>
                            Cancel
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}
      {moving && (
        <Modal title={`Call ${name(moving)} at`} onClose={() => setMoving(null)}>
          <form className="form" onSubmit={(e) => { e.preventDefault(); move.mutate(); }}>
            <span className="hint">The time must be inside the calling hours of "{moving.campaign_name}".</span>
            <ErrorBox error={move.error} />
            <label>Date and time <span className="hint">(your local time)</span>
              <input type="datetime-local" value={time} min={localInput(new Date())} onChange={(e) => setTime(e.target.value)} required />
            </label>
            <div className="row">
              <button className="btn" disabled={move.isPending || !time}>Save</button>
              <button type="button" className="btn ghost" onClick={() => setMoving(null)}>Close</button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
