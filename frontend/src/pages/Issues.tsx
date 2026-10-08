import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { IssueDetail, IssueReport, IssueSummary, Page } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { cleanParams } from "../hooks";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHeader, Pagination, fmtDate, type Tone } from "../components/ui";
import { Select } from "../components/Select";

const CATEGORIES: [string, string][] = [
  ["calls", "Phone calls"], ["whatsapp", "WhatsApp"], ["email", "Email"], ["campaigns", "Campaigns"],
  ["knowledge", "Knowledge base / AI answers"], ["billing", "Billing"], ["account", "Login or account"],
  ["bug", "Something looks broken"], ["other", "Something else"],
];
const CATEGORY = Object.fromEntries(CATEGORIES);
const STATUSES = ["open", "in_progress", "resolved", "closed"] as const;
const PRIORITY_TONE: Record<string, Tone> = { high: "red", normal: "blue", low: "grey" };

export function useIssueSummary(enabled = true) {
  return useQuery({
    queryKey: ["issues-summary"],
    queryFn: async () => (await api.get<IssueSummary>("/issues/summary")).data,
    refetchInterval: 60_000,
    enabled,
  });
}

/** Report a problem with the platform, and (for admins) deal with the reports addressed to you. */
export default function Issues() {
  const { user } = useAuth();
  const { data: summary } = useIssueSummary();
  const hasInbox = user?.role === "admin" || user?.role === "super_admin";
  const [box, setBox] = useState<"mine" | "inbox">(user?.role === "super_admin" ? "inbox" : "mine");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["issues", box, status, page],
    queryFn: async () => (await api.get<Page<IssueReport>>("/issues", { params: cleanParams({ box, status, page }) })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
  const pick = (b: "mine" | "inbox") => { setBox(b); setPage(1); };
  const inboxTitle = user?.role === "super_admin" ? "From companies" : "From my team";

  return (
    <>
      <PageHeader
        title={user?.role === "super_admin" ? "Issue reports" : "Report an issue"}
        subtitle={user?.role === "super_admin"
          ? "Problems that company admins reported to you. Reply, and mark them resolved when they are fixed."
          : `Something not working? Tell us here. Your reports go to ${summary?.reports_go_to ?? "your company admin"}.`}
        actions={summary?.can_report !== false && user?.role !== "super_admin" && <button className="btn" onClick={() => setCreating(true)}>+ Report an issue</button>}
      />
      <div className="filters spread row">
        {hasInbox && user?.role !== "super_admin" ? (
          <div className="tabs" style={{ marginBottom: 0 }}>
            <button className={box === "mine" ? "active" : ""} onClick={() => pick("mine")}>My reports</button>
            <button className={box === "inbox" ? "active" : ""} onClick={() => pick("inbox")}>
              {inboxTitle}{summary && summary.inbox_open > 0 ? ` (${summary.inbox_open})` : ""}
            </button>
          </div>
        ) : <span />}
        <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label="Status">
          <option value="">All statuses</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
        </Select>
      </div>
      <ErrorBox error={error} />
      {isLoading ? <Loading /> : !data?.items.length ? (
        <Empty>{box === "inbox" ? "No reports for you to deal with." : "You have not reported anything yet."}</Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>#</th><th>Subject</th><th>About</th>{box === "inbox" && <th>From</th>}<th>Priority</th><th>Status</th><th>Reported</th></tr>
              </thead>
              <tbody>
                {data.items.map((i) => (
                  <tr key={i.id} className="clickable" onClick={() => setOpen(i.id)}>
                    <td>#{i.number}</td>
                    <td className="cell-wrap" style={{ maxWidth: 420 }}>
                      <strong>{i.subject}</strong>
                      <div className="small muted">
                        {i.comment_count > 0 ? `${i.comment_count} ${i.comment_count === 1 ? "reply" : "replies"}` : "no replies yet"}
                        {i.escalated_to && ` · sent to the platform admin as #${i.escalated_to.number}`}
                        {i.escalated_from_id && " · passed on from an employee"}
                      </div>
                    </td>
                    <td>{CATEGORY[i.category] ?? i.category}</td>
                    {box === "inbox" && (
                      <td>{i.reporter?.full_name ?? "Former user"}<div className="small muted">{user?.role === "super_admin" ? i.company_name : i.reporter_role}</div></td>
                    )}
                    <td><Badge value={i.priority} tone={PRIORITY_TONE[i.priority]} /></td>
                    <td><Badge value={i.status} /></td>
                    <td className="small nowrap">{fmtDate(i.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}
      {creating && <NewReport goesTo={summary?.reports_go_to ?? "your company admin"} onClose={() => setCreating(false)} onDone={(id) => { setCreating(false); pick("mine"); setOpen(id); }} />}
      {open && <ReportView id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function NewReport({ goesTo, onClose, onDone }: { goesTo: string; onClose: () => void; onDone: (id: string) => void }) {
  const qc = useQueryClient();
  const location = useLocation();
  const [form, setForm] = useState({ subject: "", description: "", category: "other", priority: "normal" });
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));
  const create = useMutation({
    mutationFn: async () => (await api.post<IssueDetail>("/issues", { ...form, page: location.pathname })).data,
    onSuccess: (report) => {
      qc.invalidateQueries({ queryKey: ["issues"] });
      qc.invalidateQueries({ queryKey: ["issues-summary"] });
      onDone(report.id);
    },
  });
  return (
    <Modal title="Report an issue" onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
        <span className="hint">This goes to {goesTo}. Say what you were doing, what you expected, and what happened instead.</span>
        <ErrorBox error={create.error} />
        <label>Subject<input value={form.subject} onChange={(e) => set({ subject: e.target.value })} placeholder="e.g. Inbound calls drop after 10 seconds" maxLength={200} required minLength={3} /></label>
        <div className="form-row">
          <label>What is it about?
            <Select value={form.category} onChange={(e) => set({ category: e.target.value })}>
              {CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </Select>
          </label>
          <label>How urgent?
            <Select value={form.priority} onChange={(e) => set({ priority: e.target.value })}>
              <option value="low">Low: when you have time</option>
              <option value="normal">Normal</option>
              <option value="high">High: it is stopping our work</option>
            </Select>
          </label>
        </div>
        <label>What happened?
          <textarea style={{ minHeight: 150 }} value={form.description} onChange={(e) => set({ description: e.target.value })} required minLength={5} maxLength={5000}
            placeholder="When did it start? Which customer, campaign or call? What did you see?" />
        </label>
        <div className="row">
          <button className="btn" disabled={create.isPending || form.subject.trim().length < 3 || form.description.trim().length < 5}>{create.isPending ? "Sending…" : "Send report"}</button>
          <button type="button" className="btn ghost" onClick={onClose}>Close</button>
        </div>
      </form>
    </Modal>
  );
}

function ReportView({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { user } = useAuth();
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [escalating, setEscalating] = useState(false);
  const key = ["issue", id];
  const { data: report, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: async () => (await api.get<IssueDetail>(`/issues/${id}`)).data,
    refetchInterval: 15_000,
  });
  useEffect(() => { setText(""); setNote(""); setEscalating(false); }, [id]);
  const done = (updated?: IssueDetail) => {
    if (updated && updated.id === id) qc.setQueryData(key, updated);
    else qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["issues"] });
    qc.invalidateQueries({ queryKey: ["issues-summary"] });
  };
  const reply = useMutation({
    mutationFn: async () => (await api.post<IssueDetail>(`/issues/${id}/comments`, { text })).data,
    onSuccess: (d) => { setText(""); done(d); },
  });
  const setStatus = useMutation({
    mutationFn: async (status: string) => (await api.patch<IssueDetail>(`/issues/${id}`, { status })).data,
    onSuccess: done,
  });
  const escalate = useMutation({
    mutationFn: async () => (await api.post<IssueDetail>(`/issues/${id}/escalate`, { note })).data,
    onSuccess: () => { setEscalating(false); setNote(""); done(); },
  });

  return (
    <Modal title={report ? `#${report.number} · ${report.subject}` : "Report"} onClose={onClose}>
      {isLoading ? <Loading /> : error || !report ? <ErrorBox error={error} /> : (
        <div className="form">
          <div className="row">
            <Badge value={report.status} />
            <Badge value={report.priority} tone={PRIORITY_TONE[report.priority]} />
            <span className="pill">{CATEGORY[report.category] ?? report.category}</span>
            <span className="pill">{report.level === "platform" ? "to the platform admin" : "to the company admin"}</span>
          </div>
          <div className="small muted">
            {report.reporter?.full_name ?? "Former user"}{report.reporter_role ? ` (${report.reporter_role.replace("_", " ")})` : ""}
            {user?.role === "super_admin" && report.company_name ? ` · ${report.company_name}` : ""} · {fmtDate(report.created_at)}
            {report.page ? ` · on ${report.page}` : ""}
          </div>
          <pre className="issue-text">{report.description}</pre>
          {report.escalated_to && (
            <div className="note-box">
              <strong>Sent to the platform administrator</strong>
              <span>As report #{report.escalated_to.number}, which is {report.escalated_to.status.replace("_", " ")}.</span>
            </div>
          )}

          {report.comments.length > 0 && (
            <div className="chat" style={{ maxHeight: 280 }}>
              {report.comments.map((c) => c.kind === "status" ? (
                <div key={c.id} className="issue-event small muted">{c.text} · {c.author?.full_name ?? "System"} · {fmtDate(c.created_at)}</div>
              ) : (
                <div key={c.id} className={`bubble ${c.author?.id === user?.id ? "outbound" : "inbound"}`}>
                  {c.text}
                  <div className="meta">{c.author?.full_name ?? "Former user"} · {fmtDate(c.created_at)}</div>
                </div>
              ))}
            </div>
          )}

          <ErrorBox error={reply.error || setStatus.error || escalate.error} />
          {report.status === "closed" ? (
            <span className="hint">This report is closed. If the problem comes back, report it again.</span>
          ) : (
            <>
              <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={report.can_manage ? "Reply to the person who reported this…" : "Add more detail, or answer a question…"} />
              <div className="row">
                <button className="btn" disabled={!text.trim() || reply.isPending} onClick={() => reply.mutate()}>{reply.isPending ? "Sending…" : "Send reply"}</button>
                {!report.can_manage && report.status === "resolved" && <span className="hint">Replying reopens the report.</span>}
              </div>
            </>
          )}

          {report.can_manage && (
            <div className="manage-box">
              <div className="field-label" style={{ marginBottom: 8 }}>Handle this report</div>
              <div className="row">
                {STATUSES.filter((s) => s !== report.status).map((s) => (
                  <button key={s} className={`btn sm ${s === "resolved" ? "" : "ghost"}`} disabled={setStatus.isPending} onClick={() => setStatus.mutate(s)}>
                    Mark {s.replace("_", " ")}
                  </button>
                ))}
                {report.can_escalate && !escalating && <button className="btn ghost sm" onClick={() => setEscalating(true)}>Send to the platform admin…</button>}
              </div>
              {escalating && (
                <div className="form" style={{ marginTop: 12 }}>
                  <span className="hint">The platform admin gets this report with the employee's own words. Add anything you already checked.</span>
                  <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional note for the platform admin" />
                  <div className="row">
                    <button className="btn sm" disabled={escalate.isPending} onClick={() => escalate.mutate()}>{escalate.isPending ? "Sending…" : "Send to the platform admin"}</button>
                    <button className="btn ghost sm" onClick={() => setEscalating(false)}>Cancel</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
