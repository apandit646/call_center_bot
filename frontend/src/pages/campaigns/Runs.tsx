// Using a campaign again: each use is a numbered run with its own contacts, calls and results.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Campaign, CampaignRun } from "../../api/types";
import { Badge, ErrorBox, Loading, Modal, fmtDate } from "../../components/ui";

/** Which run the page is showing: "" = the current run, a run's id, or "all". */
export type RunChoice = string;
type Source = "new_list" | "same_contacts" | "not_reached";

const SOURCE_LABEL: Record<string, string> = {
  first: "First run", new_list: "New contact list", same_contacts: "Same contacts again", not_reached: "People not reached",
};

export function useRuns(campaignId: string) {
  return useQuery({
    queryKey: ["campaign-runs", campaignId],
    queryFn: async () => (await api.get<CampaignRun[]>(`/campaigns/${campaignId}/runs`)).data,
  });
}

/** The strip under the campaign's title: which run you are looking at, and "Run again". */
export function RunBar({ campaign, run, onRun, onStarted, canManage }: {
  campaign: Campaign; run: RunChoice; onRun: (run: RunChoice) => void; onStarted: (source: Source) => void; canManage: boolean;
}) {
  const { data: runs } = useRuns(campaign.id);
  const [asking, setAsking] = useState(false);
  const finished = campaign.status === "completed" || campaign.status === "cancelled";
  if (!runs || (runs.length < 2 && !finished)) return null; // a campaign on its first, unfinished run needs none of this
  const current = runs.find((r) => r.is_current);
  const shown = run === "all" ? null : runs.find((r) => r.id === run) ?? current;

  return (
    <div className="run-bar">
      <label className="run-pick">
        <span>Showing</span>
        <select value={run} onChange={(e) => onRun(e.target.value)} aria-label="Run">
          {runs.map((r) => (
            <option key={r.id} value={r.is_current ? "" : r.id}>
              Run {r.number}{r.is_current ? " (current)" : ""} · {r.contacts} contact{r.contacts === 1 ? "" : "s"}
            </option>
          ))}
          {runs.length > 1 && <option value="all">All runs together</option>}
        </select>
      </label>
      {shown && !shown.is_current && (
        <span className="muted small">
          You are looking at an earlier run ({shown.status}, {fmtDate(shown.completed_at)}). Uploading contacts and scheduling always apply to the current run.
        </span>
      )}
      {run === "all" && <span className="muted small">Numbers from every run added together.</span>}
      <span style={{ flex: 1 }} />
      {canManage && finished && <button className="btn" onClick={() => setAsking(true)}>Run again</button>}
      {asking && <RunAgain campaign={campaign} last={current} onClose={() => setAsking(false)} onStarted={(s) => { setAsking(false); onRun(""); onStarted(s); }} />}
    </div>
  );
}

function RunAgain({ campaign, last, onClose, onStarted }: {
  campaign: Campaign; last: CampaignRun | undefined; onClose: () => void; onStarted: (source: Source) => void;
}) {
  const qc = useQueryClient();
  const [source, setSource] = useState<Source>("new_list");
  const [skipCompleted, setSkipCompleted] = useState(true);
  const notReached = last ? last.contacts - last.completed - last.do_not_call : 0;
  const start = useMutation({
    mutationFn: async () => api.post(`/campaigns/${campaign.id}/runs`, { source, skip_completed: skipCompleted }),
    onSuccess: () => {
      for (const key of ["campaign", "campaign-runs", "campaign-analytics", "contacts", "calls", "results", "campaigns"]) {
        qc.invalidateQueries({ queryKey: [key] });
      }
      onStarted(source);
    },
  });
  const option = (value: Source, title: string, hint: string, disabled = false) => (
    <label className={`choice ${source === value ? "on" : ""} ${disabled ? "off" : ""}`}>
      <input type="radio" name="source" checked={source === value} disabled={disabled} onChange={() => setSource(value)} />
      <span><strong>{title}</strong><span className="hint">{hint}</span></span>
    </label>
  );

  return (
    <Modal title={`Run "${campaign.name}" again`} onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); start.mutate(); }}>
        <span className="hint">
          Run {(last?.number ?? campaign.run_number) + 1} uses the same script, questions and settings. Run {last?.number ?? campaign.run_number} and
          its results are kept as they are.
        </span>
        <ErrorBox error={start.error} />
        <div className="field-label" style={{ marginBottom: 0 }}>Who should be called?</div>
        {option("new_list", "A new contact list", "You upload the file in the next step.")}
        {option("same_contacts", "The same contacts as the last run", `Everyone from run ${last?.number ?? ""} is called again (${last?.contacts ?? 0} ${last?.contacts === 1 ? "contact" : "contacts"}).`, !last?.contacts)}
        {option("not_reached", "Only the people not reached last time", `${notReached} of ${last?.contacts ?? 0} did not complete the last run.`, notReached <= 0)}
        {source === "new_list" && (
          <label className="checkbox">
            <input type="checkbox" checked={skipCompleted} onChange={(e) => setSkipCompleted(e.target.checked)} />
            Leave out people who already completed this campaign in an earlier run
          </label>
        )}
        <span className="hint">Anyone who asked not to be called again is always left out.</span>
        <div className="row">
          <button className="btn" disabled={start.isPending}>{start.isPending ? "Starting…" : source === "new_list" ? "Start and upload contacts" : "Start and go to scheduling"}</button>
          <button type="button" className="btn ghost" onClick={onClose}>Close</button>
        </div>
      </form>
    </Modal>
  );
}

/** The "Runs" tab: every time the campaign was used, and how it went. */
export function RunsTab({ campaign, onView }: { campaign: Campaign; onView: (run: RunChoice) => void }) {
  const { data: runs, isLoading, error } = useRuns(campaign.id);
  if (isLoading) return <Loading />;
  if (error || !runs) return <ErrorBox error={error} />;
  return (
    <>
      <p className="muted" style={{ margin: "0 4px 14px" }}>
        Each time this campaign is used is a run, with its own contacts, calls and results. When a run has completed or
        been cancelled, "Run again" starts the next one with the same script.
      </p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Run</th><th>Who was called</th><th>Status</th><th>Contacts</th><th>Completed</th><th>Not reached</th><th>Calls</th><th>Started</th><th /></tr></thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td><strong>Run {r.number}</strong>{r.is_current && <div className="small muted">current</div>}</td>
                <td className="cell-wrap">
                  {SOURCE_LABEL[r.source] ?? r.source}
                  {(r.skipped_do_not_call > 0 || r.skipped_completed > 0) && (
                    <div className="small muted">
                      left out: {[r.skipped_do_not_call > 0 && `${r.skipped_do_not_call} do-not-call`, r.skipped_completed > 0 && `${r.skipped_completed} already completed`].filter(Boolean).join(", ")}
                    </div>
                  )}
                </td>
                <td>{r.status ? <Badge value={r.status} /> : "—"}</td>
                <td>{r.contacts}</td>
                <td>{r.completed} <span className="muted small">({Math.round(r.completion_rate * 100)}%)</span></td>
                <td>{r.failed + r.waiting}{r.do_not_call > 0 && <div className="small muted">{r.do_not_call} do-not-call</div>}</td>
                <td>{r.calls}</td>
                <td className="small nowrap">{fmtDate(r.started_at)}</td>
                <td><div className="cell-actions"><button className="btn ghost sm" onClick={() => onView(r.is_current ? "" : r.id)}>View results</button></div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
