import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { api, downloadFile } from "../../api/client";
import type {
  Call, Campaign, CampaignAnalytics, Contact, ImportPreview, OfferingType, Page, Question, ResultRow,
} from "../../api/types";
import { cleanParams } from "../../hooks";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Pagination, Stat, fmtDate, fmtValue } from "../../components/ui";
import { useAuth } from "../../auth/AuthContext";
import QuestionEditor, { LANGUAGE_NAMES, questionPayload } from "./QuestionEditor";
import { RunBar, RunsTab, type RunChoice } from "./Runs";
import { useConfirm } from "../../components/Confirm";
import { Select } from "../../components/Select";

type Tab = "overview" | "about" | "contacts" | "questions" | "schedule" | "calls" | "results" | "runs";
const OFFERING_LABEL: Record<OfferingType, string> = { product: "A product", service: "A service", general: "General (no specific product)" };

export default function CampaignDetail() {
  const ask = useConfirm();
  const { id } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("overview");
  const [run, setRun] = useState<RunChoice>("");  // "" = the current run
  const { hasRole } = useAuth();

  const { data: campaign, isLoading, error } = useQuery({
    queryKey: ["campaign", id],
    queryFn: async () => (await api.get<Campaign>(`/campaigns/${id}`)).data,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 10_000 : false),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["campaign", id] });
    qc.invalidateQueries({ queryKey: ["campaign-analytics", id] });
    qc.invalidateQueries({ queryKey: ["campaign-runs", id] });
    qc.invalidateQueries({ queryKey: ["campaigns"] });
  };
  const lifecycle = useMutation({
    mutationFn: async (action: "pause" | "resume" | "cancel" | "delete") =>
      action === "delete" ? api.delete(`/campaigns/${id}`) : api.post(`/campaigns/${id}/${action}`),
    onSuccess: (_, action) => (action === "delete" ? navigate("/campaigns") : refresh()),
  });

  const duplicate = useMutation({
    mutationFn: async () => (await api.post<Campaign>(`/campaigns/${id}/duplicate`)).data,
    onSuccess: (copy) => {
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      navigate(`/campaigns/${copy.id}`);
    },
  });

  if (isLoading) return <Loading />;
  if (error || !campaign) return <ErrorBox error={error} />;
  const s = campaign.status;

  return (
    <>
      <PageHeader
        title={<>{campaign.name} <Badge value={s} />{campaign.run_number > 1 && <span className="pill">Run {campaign.run_number}</span>}</>}
        subtitle={campaign.description}
        actions={
          <>
            <button className="btn ghost" disabled={duplicate.isPending} title="A new draft with the same script, questions and settings, and no contacts"
              onClick={() => duplicate.mutate()}>
              {duplicate.isPending ? "Duplicating…" : "Duplicate"}
            </button>
            {(s === "running" || s === "scheduled") && <button className="btn ghost" onClick={() => lifecycle.mutate("pause")}>Pause</button>}
            {s === "paused" && <button className="btn" onClick={() => lifecycle.mutate("resume")}>Resume</button>}
            {!["completed", "cancelled", "draft"].includes(s) && (
              <button className="btn danger" onClick={async () => (await ask({ title: "Cancel this campaign?", message: "Calls that have not been made yet will not be made. This cannot be undone.", confirm: "Cancel campaign", cancel: "Keep it", danger: true })) && lifecycle.mutate("cancel")}>
                Cancel
              </button>
            )}
            {s === "draft" && (
              <button className="btn danger" onClick={async () => (await ask({ title: "Delete this draft campaign?", message: "Its questions and uploaded contacts are deleted too.", confirm: "Delete campaign", danger: true })) && lifecycle.mutate("delete")}>Delete</button>
            )}
          </>
        }
      />
      <ErrorBox error={lifecycle.error || duplicate.error} />
      <div className="tabs">
        {(["overview", "about", "contacts", "questions", "schedule", "calls", "results", "runs"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
        ))}
      </div>
      {["overview", "contacts", "calls", "results"].includes(tab) && (
        <RunBar campaign={campaign} run={run} onRun={setRun} canManage={hasRole("admin", "supervisor")}
          onStarted={(source) => { refresh(); setTab(source === "new_list" ? "contacts" : "schedule"); }} />
      )}
      {tab === "runs" && <RunsTab campaign={campaign} onView={(r) => { setRun(r); setTab("results"); }} />}
      {tab === "overview" && <Overview campaign={campaign} run={run} />}
      {tab === "about" && <About campaign={campaign} onSaved={refresh} />}
      {tab === "contacts" && <Contacts campaign={campaign} onChange={refresh} run={run} />}
      {tab === "questions" && <Questions campaign={campaign} onSaved={refresh} />}
      {tab === "schedule" && <Schedule campaign={campaign} onSaved={refresh} />}
      {tab === "calls" && <Calls campaign={campaign} run={run} />}
      {tab === "results" && <Results campaign={campaign} run={run} />}
    </>
  );
}

// ------------------------------------------------------------------ overview / analytics
function Overview({ campaign, run }: { campaign: Campaign; run: RunChoice }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["campaign-analytics", campaign.id, run],
    queryFn: async () => (await api.get<CampaignAnalytics>(`/campaigns/${campaign.id}/analytics`, { params: cleanParams({ run }) })).data,
    refetchInterval: campaign.status === "running" ? 10_000 : false,
  });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const c = data.contacts;

  return (
    <>
      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat label="Contacts" value={c.total} />
        <Stat label="Completed" value={c.completed ?? 0} hint={`${Math.round(data.completion_rate * 100)}% completion`} />
        <Stat label="Scheduled / in progress" value={(c.scheduled ?? 0) + (c.in_progress ?? 0) + (c.pending ?? 0)} />
        <Stat label="Failed (max attempts)" value={c.failed ?? 0} />
        <Stat label="Calls placed" value={data.calls.total} />
        <Stat label="No answer / busy" value={(data.calls.no_answer ?? 0) + (data.calls.busy ?? 0)} />
        <Stat label="Avg. call duration" value={data.avg_call_duration_seconds ? `${Math.round(data.avg_call_duration_seconds)}s` : "—"} />
        <Stat label="Call window" value={`${campaign.call_window_start.slice(0, 5)}–${campaign.call_window_end.slice(0, 5)}`} hint={campaign.timezone} />
      </div>
      <div className="grid grid-2">
        {data.questions.map((q) => {
          const dist = Object.entries(q.distribution ?? {}).sort((a, b) => b[1] - a[1]);
          const max = Math.max(1, ...dist.map(([, n]) => n));
          return (
            <div className="card" key={q.question_id}>
              <h3>{q.text}</h3>
              <div className="small muted">{q.answer_type.replace("_", "/")} · {q.answered} answered of {q.responses} asked{q.average != null && ` · avg ${q.average}`}</div>
              <div style={{ marginTop: 10 }}>
                {dist.map(([value, n]) => (
                  <div key={value} style={{ marginBottom: 6 }}>
                    <div className="row spread small"><span>{value === "true" ? "Yes" : value === "false" ? "No" : value}</span><span>{n}</span></div>
                    <div className="bar"><div style={{ width: `${(100 * n) / max}%` }} /></div>
                  </div>
                ))}
                {q.samples?.map((sample, i) => <div key={i} className="small" style={{ padding: "4px 0" }}>“{sample}”</div>)}
                {!dist.length && !q.samples?.length && <div className="muted small">No answers yet.</div>}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

// ------------------------------------------------------------------ about: what the AI knows
function About({ campaign, onSaved }: { campaign: Campaign; onSaved: () => void }) {
  const [form, setForm] = useState({
    offering_type: campaign.offering_type ?? "general",
    offering_name: campaign.offering_name ?? "",
    offering_details: campaign.offering_details ?? "",
    description: campaign.description ?? "",
  });
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));
  const editable = ["draft", "scheduled", "paused"].includes(campaign.status);
  const save = useMutation({
    mutationFn: async () =>
      api.patch(`/campaigns/${campaign.id}`, {
        offering_type: form.offering_type,
        offering_name: form.offering_type === "general" ? null : form.offering_name || null,
        offering_details: form.offering_details || null,
        description: form.description || null,
      }),
    onSuccess: onSaved,
  });

  return (
    <div className="card form">
      <h2>What the AI knows on this call</h2>
      <span className="muted small">
        When a customer asks "which company is this?" or "what is this product?", the agent answers from your company profile
        (Company Settings) and from what you write here. It never states anything else as fact.
      </span>
      {!editable && <p className="muted">Pause the campaign to edit these.</p>}
      <ErrorBox error={save.error} />
      {save.isSuccess && <div className="success">Saved.</div>}
      <div className="form-row">
        <label>
          This campaign is about
          <Select value={form.offering_type} disabled={!editable} onChange={(e) => set({ offering_type: e.target.value as OfferingType })}>
            {(Object.keys(OFFERING_LABEL) as OfferingType[]).map((t) => <option key={t} value={t}>{OFFERING_LABEL[t]}</option>)}
          </Select>
        </label>
        {form.offering_type !== "general" && (
          <label>
            {form.offering_type === "product" ? "Product name" : "Service name"}
            <input value={form.offering_name} disabled={!editable} onChange={(e) => set({ offering_name: e.target.value })} />
          </label>
        )}
      </div>
      <label>
        {form.offering_type === "general" ? "Details the AI may share" : `About the ${form.offering_type}`}
        <textarea style={{ minHeight: 120 }} value={form.offering_details} disabled={!editable}
          placeholder="Features, price, who it is for." onChange={(e) => set({ offering_details: e.target.value })} />
      </label>
      <label>
        Why you are calling
        <textarea value={form.description} disabled={!editable} onChange={(e) => set({ description: e.target.value })} />
      </label>
      {editable && <div><button className="btn" disabled={save.isPending} onClick={() => save.mutate()}>Save</button></div>}
    </div>
  );
}

// ------------------------------------------------------------------ contacts + Excel import
function Contacts({ campaign, onChange, run }: { campaign: Campaign; onChange: () => void; run: RunChoice }) {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const closed = campaign.status === "completed" || campaign.status === "cancelled";

  const { data, isLoading, error } = useQuery({
    queryKey: ["contacts", campaign.id, page, status, run],
    queryFn: async () =>
      (await api.get<Page<Contact>>(`/campaigns/${campaign.id}/contacts`, { params: cleanParams({ page, status, run }) })).data,
    placeholderData: keepPreviousData,
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return (await api.post<ImportPreview>(`/campaigns/${campaign.id}/imports`, form)).data;
    },
    onSuccess: setPreview,
  });
  const decide = useMutation({
    mutationFn: async (action: "confirm" | "discard") =>
      (await api.post<ImportPreview>(`/campaigns/${campaign.id}/imports/${preview!.id}/${action}`)).data,
    onSuccess: (p) => {
      setPreview(p.status === "confirmed" ? p : null);
      qc.invalidateQueries({ queryKey: ["contacts", campaign.id] });
      onChange();
    },
  });

  return (
    <>
      {!closed && (
        <div className="card">
          <h2>Import contacts from Excel</h2>
          <p className="muted small">
            .xlsx or .csv with a header row. Required column: <code>phone</code>. Recognised: <code>name</code>, <code>email</code>,{" "}
            <code>city</code>. Any other column (e.g. <code>product</code>) is kept and can be used in the greeting as {"{product}"}.
          </p>
          <input type="file" accept=".xlsx,.csv" disabled={upload.isPending}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
          <ErrorBox error={upload.error || decide.error} />

          {preview && (
            <div style={{ marginTop: 16 }}>
              {preview.status === "confirmed" && <div className="success">Import confirmed — contacts added to the campaign.</div>}
              <div className="grid grid-4" style={{ marginBottom: 12 }}>
                <Stat label="Total records" value={preview.total_rows} />
                <Stat label="Valid" value={preview.valid_rows} />
                <Stat label="Invalid" value={preview.invalid_rows} />
                <Stat label="Duplicate" value={preview.duplicate_rows} />
              </div>
              {preview.invalid_data.length > 0 && (
                <details style={{ marginBottom: 12 }}>
                  <summary>Show invalid rows ({preview.invalid_data.length})</summary>
                  <div className="table-wrap"><table>
                    <thead><tr><th>Row</th><th>Data</th><th>Errors</th></tr></thead>
                    <tbody>{preview.invalid_data.slice(0, 200).map((r) => (
                      <tr key={r.row}><td>{r.row}</td><td className="small">{JSON.stringify(r.data)}</td><td className="small">{r.errors.join("; ")}</td></tr>
                    ))}</tbody>
                  </table></div>
                </details>
              )}
              {preview.status === "preview" && (
                <>
                  <h3>Preview (first {preview.valid_sample.length} valid rows)</h3>
                  <div className="table-wrap"><table>
                    <thead><tr><th>Row</th><th>Name</th><th>Phone (normalised)</th><th>City</th><th>Email</th></tr></thead>
                    <tbody>{preview.valid_sample.map((r) => (
                      <tr key={r.row}><td>{r.row}</td><td>{r.name ?? "—"}</td><td>{r.phone}</td><td>{r.city ?? "—"}</td><td>{r.email ?? "—"}</td></tr>
                    ))}</tbody>
                  </table></div>
                  <div className="row">
                    <button className="btn" disabled={!preview.valid_rows || decide.isPending} onClick={() => decide.mutate("confirm")}>
                      Confirm & add {preview.valid_rows} contacts
                    </button>
                    <button className="btn ghost" disabled={decide.isPending} onClick={() => decide.mutate("discard")}>Discard</button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      <div className="filters">
        <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All contacts</option>
          {["pending", "scheduled", "in_progress", "completed", "failed"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
        </Select>
      </div>
      <ErrorBox error={error} />
      {isLoading ? <Loading /> : !data?.items.length ? <Empty>No contacts yet — import an Excel file above.</Empty> : (
        <>
          <div className="table-wrap"><table>
            <thead><tr><th>Name</th><th>Phone</th><th>Status</th><th>Attempts</th><th>Last attempt</th><th>Extra fields</th></tr></thead>
            <tbody>{data.items.map((c) => (
              <tr key={c.id}>
                <td>{c.customer.name ?? "—"}</td><td>{c.customer.phone}</td>
                <td><Badge value={c.status} />{c.callback_at && <div className="small muted" style={{ marginTop: 4 }}>asked to be called back {fmtDate(c.callback_at)}</div>}</td>
                <td>{c.attempts}</td><td className="small">{fmtDate(c.last_attempt_at)}</td>
                <td className="small">{Object.entries(c.variables).map(([k, v]) => `${k}: ${fmtValue(v)}`).join(", ") || "—"}</td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------ questions
function Questions({ campaign, onSaved }: { campaign: Campaign; onSaved: () => void }) {
  const ask = useConfirm();
  const [questions, setQuestions] = useState<Question[]>(campaign.questions);
  const [script, setScript] = useState(campaign.translations ?? {});
  const editable = campaign.status === "draft" || campaign.status === "paused" || campaign.status === "scheduled";
  const questionsEditable = campaign.status === "draft" || campaign.status === "paused";
  const alt = (campaign.alt_languages ?? []).filter((l) => l !== campaign.language);

  // Pick up server-side changes (e.g. after auto-translate).
  useEffect(() => {
    setQuestions(campaign.questions);
    setScript(campaign.translations ?? {});
  }, [campaign]);

  const save = useMutation({
    mutationFn: async () => {
      if (questionsEditable) await api.put(`/campaigns/${campaign.id}/questions`, questionPayload(questions));
      if (alt.length) await api.patch(`/campaigns/${campaign.id}`, { translations: script });
    },
    onSuccess: onSaved,
  });
  const translate = useMutation({
    mutationFn: async (overwrite: boolean) =>
      (await api.post<Campaign>(`/campaigns/${campaign.id}/translate`, null, { params: { overwrite } })).data,
    onSuccess: onSaved,
  });
  const setLine = (lang: string, field: "greeting" | "closing", value: string) =>
    setScript((s) => ({ ...s, [lang]: { ...s[lang], [field]: value } }));

  return (
    <div className="card form">
      {!questionsEditable && <p className="muted">Questions can only be edited while the campaign is a draft or paused.</p>}
      <ErrorBox error={save.error || translate.error} />
      {save.isSuccess && <div className="success">Saved.</div>}
      {translate.isSuccess && <div className="success">Translations generated — review them below, then save.</div>}

      {alt.length > 0 && (
        <div className="question-card form">
          <div className="row spread">
            <strong>Language switching</strong>
            {editable && (
              <div className="row">
                <button className="btn ghost sm" disabled={translate.isPending} onClick={() => translate.mutate(false)}>
                  {translate.isPending ? "Translating…" : "Auto-translate missing"}
                </button>
                <button className="btn ghost sm" disabled={translate.isPending}
                  onClick={async () => (await ask({ title: "Replace all translations?", message: "Every translated line is written again from the English text, including ones you edited by hand.", confirm: "Replace translations" })) && translate.mutate(true)}>
                  Re-translate all
                </button>
              </div>
            )}
          </div>
          <p className="muted small">
            Calls start in {LANGUAGE_NAMES[campaign.language] ?? campaign.language}. When the customer answers in{" "}
            {alt.map((l) => LANGUAGE_NAMES[l] ?? l).join(" / ")}, the rest of the call switches to that language.
            Blank lines are auto-translated when the campaign is scheduled; until then the original text is used.
          </p>
          {alt.map((lang) => (
            <div key={lang} className="form">
              <label>
                Greeting ({LANGUAGE_NAMES[lang] ?? lang})
                <textarea value={script[lang]?.greeting ?? ""} disabled={!editable}
                  placeholder={campaign.greeting_script} onChange={(e) => setLine(lang, "greeting", e.target.value)} />
              </label>
              <label>
                Closing ({LANGUAGE_NAMES[lang] ?? lang})
                <textarea value={script[lang]?.closing ?? ""} disabled={!editable}
                  placeholder={campaign.closing_script} onChange={(e) => setLine(lang, "closing", e.target.value)} />
              </label>
            </div>
          ))}
        </div>
      )}

      <QuestionEditor questions={questions} onChange={setQuestions} disabled={!questionsEditable} altLanguages={alt} />
      {editable && (
        <div>
          <button className="btn" disabled={save.isPending || questions.some((q) => !q.text || !q.key)} onClick={() => save.mutate()}>
            Save
          </button>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ schedule
function toLocalInput(iso: string | null) {
  // Default to "now" (calls start immediately); a start time already in the past also means now.
  const d = iso && new Date(iso).getTime() > Date.now() ? new Date(iso) : new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function Schedule({ campaign, onSaved }: { campaign: Campaign; onSaved: () => void }) {
  const [form, setForm] = useState({
    start_at: toLocalInput(campaign.start_at),
    timezone: campaign.timezone,
    call_window_start: campaign.call_window_start.slice(0, 5),
    call_window_end: campaign.call_window_end.slice(0, 5),
    max_concurrent_calls: campaign.max_concurrent_calls,
    max_attempts: campaign.max_attempts,
    retry_delay_minutes: campaign.retry_delay_minutes,
    conversational: campaign.conversational ?? true,
    agent_prompt: campaign.agent_prompt ?? "",
  });
  useEffect(() => setForm((f) => ({ ...f, start_at: toLocalInput(campaign.start_at) })), [campaign.start_at]);
  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));
  const { start_at, ...settings } = form;
  const canSchedule = ["draft", "scheduled", "paused"].includes(campaign.status);

  const saveSettings = useMutation({ mutationFn: async () => api.patch(`/campaigns/${campaign.id}`, settings), onSuccess: onSaved });
  const schedule = useMutation({
    mutationFn: async () => api.post(`/campaigns/${campaign.id}/schedule`, { start_at: new Date(start_at).toISOString(), settings }),
    onSuccess: onSaved,
  });

  return (
    <div className="card form">
      <h2>Call schedule, retry rules &amp; concurrency</h2>
      <ErrorBox error={schedule.error || saveSettings.error} />
      {schedule.isSuccess && <div className="success">Scheduled. The scheduler will start dialling at the start time, inside the call window.</div>}
      <div className="form-row">
        <label>Start at (your local time)<input type="datetime-local" value={start_at} onChange={(e) => set({ start_at: e.target.value })} /></label>
        <label>Campaign timezone<input value={form.timezone} onChange={(e) => set({ timezone: e.target.value })} placeholder="Asia/Kolkata" /></label>
        <label>Call window start<input type="time" value={form.call_window_start} onChange={(e) => set({ call_window_start: e.target.value })} /></label>
        <label>Call window end<input type="time" value={form.call_window_end} onChange={(e) => set({ call_window_end: e.target.value })} /></label>
      </div>
      <div className="form-row">
        <label>Max concurrent calls<input type="number" min={1} max={200} value={form.max_concurrent_calls} onChange={(e) => set({ max_concurrent_calls: +e.target.value })} /></label>
        <label>Max attempts per contact<input type="number" min={1} max={10} value={form.max_attempts} onChange={(e) => set({ max_attempts: +e.target.value })} /></label>
        <label>Retry delay (minutes)<input type="number" min={1} value={form.retry_delay_minutes} onChange={(e) => set({ retry_delay_minutes: +e.target.value })} /></label>
      </div>
      <div className="form-row">
        <label className="checkbox">
          <input type="checkbox" checked={form.conversational} onChange={(e) => set({ conversational: e.target.checked })} />
          Natural conversation (AI talks like a person, with follow-ups)
        </label>
      </div>
      <div className="form">
        {form.conversational && (
          <label>
            AI personality &amp; conversation rules <span className="muted small">(blank = default persona)</span>
            <textarea style={{ minHeight: 200, fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12.5 }}
              value={form.agent_prompt} onChange={(e) => set({ agent_prompt: e.target.value })} />
          </label>
        )}
      </div>
      <div className="row">
        {canSchedule && (
          <button className="btn" disabled={schedule.isPending} onClick={() => schedule.mutate()}>
            {campaign.status === "draft" ? "Schedule calls" : "Schedule newly imported contacts"}
          </button>
        )}
        {["draft", "scheduled", "paused"].includes(campaign.status) && (
          <button className="btn ghost" disabled={saveSettings.isPending} onClick={() => saveSettings.mutate()}>Save settings only</button>
        )}
      </div>
      <p className="muted small">
        Calls run only inside the call window (campaign timezone). Unanswered, busy or failed calls are retried after the
        retry delay until the max attempts is reached.
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ calls
function Calls({ campaign, run }: { campaign: Campaign; run: RunChoice }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useQuery({
    queryKey: ["calls", campaign.id, page, run],
    queryFn: async () => (await api.get<Page<Call>>(`/campaigns/${campaign.id}/calls`, { params: cleanParams({ page, run }) })).data,
    placeholderData: keepPreviousData,
    refetchInterval: campaign.status === "running" ? 5_000 : false,
  });
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data?.items.length) return <Empty>No calls placed yet.</Empty>;
  return (
    <>
      <div className="table-wrap"><table>
        <thead><tr><th>Customer</th><th>Attempt</th><th>Status</th><th>All answered</th><th>Duration</th><th>Started</th></tr></thead>
        <tbody>{data.items.map((c) => (
          <tr key={c.id} className="clickable" onClick={() => navigate(`/calls/${c.id}`)}>
            <td>{c.customer.name ?? "—"}<div className="small muted">{c.customer.phone}</div></td>
            <td>{c.attempt_number}</td><td><Badge value={c.status} /></td>
            <td>{c.all_questions_answered ? "Yes" : "—"}</td>
            <td>{c.duration_seconds != null ? `${c.duration_seconds}s` : "—"}</td>
            <td className="small">{fmtDate(c.started_at)}</td>
          </tr>
        ))}</tbody>
      </table></div>
      <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
    </>
  );
}

// ------------------------------------------------------------------ results
function Results({ campaign, run }: { campaign: Campaign; run: RunChoice }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [exporting, setExporting] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["results", campaign.id, page, run],
    queryFn: async () => (await api.get<Page<ResultRow>>(`/campaigns/${campaign.id}/results`, { params: cleanParams({ page, run }) })).data,
    placeholderData: keepPreviousData,
  });
  // The current run shows the current script's questions; an earlier run (or all runs) shows the
  // questions that actually have answers in these rows, which may include ones since replaced.
  const answered = [...new Set((data?.items ?? []).flatMap((r) => Object.keys(r.answers)))];
  const keys = run ? answered : [...new Set([...campaign.questions.map((q) => q.key), ...answered])];

  const exportXlsx = async () => {
    setExporting(true);
    try {
      await downloadFile(`/campaigns/${campaign.id}/results/export${run ? `?run=${run}` : ""}`, `${campaign.name}-results.xlsx`);
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <div className="filters"><button className="btn" onClick={exportXlsx} disabled={exporting}>{exporting ? "Exporting…" : "Export to Excel"}</button></div>
      <ErrorBox error={error} />
      {isLoading ? <Loading /> : !data?.items.length ? <Empty>No results yet.</Empty> : (
        <>
          <div className="table-wrap"><table>
            <thead><tr><th>Customer</th><th>Status</th>{keys.map((k) => <th key={k}>{k}</th>)}</tr></thead>
            <tbody>{data.items.map((r) => (
              <tr key={r.contact_id} className={r.call_id ? "clickable" : ""} onClick={() => r.call_id && navigate(`/calls/${r.call_id}`)}>
                <td>{r.customer.name ?? "—"}<div className="small muted">{r.customer.phone}</div></td>
                <td><Badge value={r.status} /></td>
                {keys.map((k) => <td key={k}>{fmtValue(r.answers[k])}</td>)}
              </tr>
            ))}</tbody>
          </table></div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}
