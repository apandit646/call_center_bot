import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Callback, Page } from "../../api/types";
import { cleanParams } from "../../hooks";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHeader, Pagination, fmtDate } from "../../components/ui";
import CampaignCallbacks from "./CampaignCallbacks";

/** A date-time for <input type="datetime-local">, in the viewer's own time. */
const localInput = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);

/** Every call-back a customer was promised, in one place: asked for on an inbound call, or on a campaign call. */
export default function Callbacks() {
  const [tab, setTab] = useState<"inbound" | "campaign">("inbound");
  return (
    <>
      <PageHeader title="Callbacks" subtitle="Customers who asked to be called back. Each is called at the time shown; a late one says why." />
      <div className="tabs">
        <button className={tab === "inbound" ? "active" : ""} onClick={() => setTab("inbound")}>From inbound calls</button>
        <button className={tab === "campaign" ? "active" : ""} onClick={() => setTab("campaign")}>From campaign calls</button>
      </div>
      {tab === "inbound" ? <InboundCallbacks /> : <CampaignCallbacks />}
    </>
  );
}

/** Calls customers asked for on an inbound call ("call me at 5 pm"). The AI agent dials each one when it is due. */
function InboundCallbacks() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [moving, setMoving] = useState<Callback | null>(null);
  const [when, setWhen] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["callbacks", status, page],
    queryFn: async () => (await api.get<Page<Callback>>("/callbacks", { params: cleanParams({ status, page }) })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["callbacks"] });
  const cancel = useMutation({ mutationFn: async (id: string) => api.post(`/callbacks/${id}/cancel`), onSuccess: refresh });
  const move = useMutation({
    mutationFn: async () => api.patch(`/callbacks/${moving!.id}`, { due_at: new Date(when).toISOString() }),
    onSuccess: () => { setMoving(null); refresh(); },
  });
  const open = (c: Callback) => {
    move.reset();
    setMoving(c);
    setWhen(localInput(new Date(Math.max(Date.now() + 10 * 60_000, new Date(c.due_at).getTime()))));
  };

  return (
    <>
      <div className="filters">
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status">
          <option value="">All callbacks</option>
          <option value="scheduled">Waiting</option>
          <option value="calling">Calling now</option>
          <option value="completed">Completed</option>
          <option value="failed">Not reached</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>
      <ErrorBox error={error || cancel.error} />
      {isLoading ? <Loading /> : !data?.items.length ? (
        <Empty>No callbacks yet. When a caller says "call me back at 5", it appears here.</Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Customer</th><th>Call at</th><th>Status</th><th>What they wanted</th><th /></tr></thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id}>
                    <td>{c.customer.name ?? c.phone}{c.customer.name && <div className="small muted">{c.phone}</div>}</td>
                    <td className="nowrap">{fmtDate(c.due_at)}{c.attempts > 0 && <div className="small muted">{c.attempts} {c.attempts === 1 ? "try" : "tries"}</div>}</td>
                    <td className="cell-wrap">
                      <Badge value={c.status === "failed" ? "not_reached" : c.status} tone={c.status === "failed" ? "red" : undefined} />
                      {c.error && c.status !== "completed" && <div className="small muted" style={{ marginTop: 6 }}>{c.error}</div>}
                    </td>
                    <td className="cell-wrap small" style={{ maxWidth: 380 }}>{c.reason ?? "—"}</td>
                    <td>
                      <div className="cell-actions">
                        {c.conversation_id && <Link className="btn ghost sm" to={`/conversations/${c.conversation_id}`}>Open call</Link>}
                        {!c.conversation_id && c.source_conversation_id && <Link className="btn ghost sm" to={`/conversations/${c.source_conversation_id}`}>Where they asked</Link>}
                        {["scheduled", "failed", "cancelled"].includes(c.status) && (
                          <button className="btn ghost sm" onClick={() => open(c)}>{c.status === "scheduled" ? "Change time" : "Schedule again"}</button>
                        )}
                        {c.status === "scheduled" && (
                          <button className="btn danger sm" disabled={cancel.isPending}
                            onClick={() => confirm(`Cancel the callback to ${c.customer.name ?? c.phone}?`) && cancel.mutate(c.id)}>
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
        <Modal title={`Call ${moving.customer.name ?? moving.phone} at`} onClose={() => setMoving(null)}>
          <form className="form" onSubmit={(e) => { e.preventDefault(); move.mutate(); }}>
            <ErrorBox error={move.error} />
            <label>Date and time <span className="hint">(your local time)</span>
              <input type="datetime-local" value={when} min={localInput(new Date())} onChange={(e) => setWhen(e.target.value)} required />
            </label>
            <div className="row">
              <button className="btn" disabled={move.isPending || !when}>Save</button>
              <button type="button" className="btn ghost" onClick={() => setMoving(null)}>Close</button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
