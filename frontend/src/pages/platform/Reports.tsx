import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { PlatformReport } from "../../api/types";
import { money } from "../../components/Billing";
import { Bars, Columns, Legend, TwoLines, shortDay, weekday } from "../../components/Charts";
import { IconDownload } from "../../components/Icons";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Stat } from "../../components/ui";

type Row = PlatformReport["companies"][number];
type SortKey = "name" | "calls" | "call_minutes" | "average_cost_per_call" | "usage_amount" | "tickets" | "ai_resolved_rate" | "invoiced" | "amount_due";
const RANGES = [7, 30, 90] as const;
const CHANNEL: Record<string, string> = { whatsapp: "WhatsApp", phone: "Phone calls", email: "Email" };
const COLUMNS: { key: SortKey; label: string; csv: string }[] = [
  { key: "calls", label: "Calls", csv: "Calls" },
  { key: "call_minutes", label: "Minutes", csv: "Call minutes" },
  { key: "average_cost_per_call", label: "Average cost per call", csv: "Average cost per call" },
  { key: "usage_amount", label: "Used", csv: "Usage charged" },
  { key: "tickets", label: "Tickets", csv: "Tickets created" },
  { key: "ai_resolved_rate", label: "Resolved by AI", csv: "Resolved by AI (%)" },
  { key: "invoiced", label: "Invoiced", csv: "Invoiced" },
  { key: "amount_due", label: "Unpaid", csv: "Unpaid now" },
];
const monthName = (ym: string, long = false) =>
  new Date(`${ym}-01T00:00:00`).toLocaleDateString(undefined, long ? { month: "long", year: "numeric" } : { month: "short" });
const percent = (rate: number | null) => (rate === null ? "—" : `${Math.round(rate * 100)}%`);
const compact = (v: number) => new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(v);

/** Save the company table as a CSV file that opens in Excel. Nothing is sent anywhere. */
function download(report: PlatformReport) {
  const cell = (v: string | number | null) => {
    const text = v === null ? "" : String(v);
    // A leading = + - @ would be run as a formula by a spreadsheet; company names are typed by people.
    const safe = /^[=+\-@\t\r]/.test(text) && typeof v === "string" ? `'${text}` : text;
    return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  const lines = [
    ["Company", "Status", "Currency", "Inbound calls", "Campaign calls", ...COLUMNS.map((c) => c.csv), "Conversations"],
    ...report.companies.map((c) => [
      c.name, c.is_active ? "active" : "suspended", c.currency ?? "", c.inbound_calls, c.campaign_calls, c.calls, c.call_minutes, c.average_cost_per_call,
      c.usage_amount, c.tickets, c.ai_resolved_rate === null ? null : Math.round(c.ai_resolved_rate * 100), c.invoiced, c.amount_due, c.conversations,
    ]),
  ];
  const blob = new Blob(["﻿" + lines.map((l) => l.map(cell).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `platform-report-${report.from}-to-${report.to}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/** The platform admin's reports: calls, money and support across every company. */
export default function PlatformReportsPage() {
  const navigate = useNavigate();
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const [sort, setSort] = useState<{ key: SortKey; down: boolean }>({ key: "calls", down: true });
  const { data, isLoading, error } = useQuery({
    queryKey: ["platform-reports", days],
    queryFn: async () => (await api.get<PlatformReport>("/platform/reports", { params: { days } })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const t = data.totals, cur = data.currency;
  const rows = [...data.companies].sort((a, b) => {
    const x = a[sort.key], y = b[sort.key];
    const order = typeof x === "string" && typeof y === "string" ? x.localeCompare(y) : (x === null ? -1 : (x as number)) - (y === null ? -1 : (y as number));
    return (sort.down ? -order : order) || a.name.localeCompare(b.name);
  });
  const head = (key: SortKey, label: string, numeric = true) => (
    <th key={key} className={`sortable ${numeric ? "num" : ""}`} aria-sort={sort.key === key ? (sort.down ? "descending" : "ascending") : undefined}>
      <button onClick={() => setSort({ key, down: sort.key === key ? !sort.down : numeric })} title={`Sort by ${label.toLowerCase()}`}>
        {label}{sort.key === key && <span aria-hidden>{sort.down ? " ↓" : " ↑"}</span>}
      </button>
    </th>
  );
  const hasMoney = data.months.some((m) => m.invoiced || m.paid);

  return (
    <>
      <PageHeader title="Reports" subtitle={`${shortDay(data.from)} to ${shortDay(data.to)}, every company together. Days are counted in ${data.timezone} time.`}
        actions={<button className="btn ghost" onClick={() => download(data)} disabled={!data.companies.length}><IconDownload size={16} /> Download CSV</button>} />
      <div className="filters">
        <div className="tabs" style={{ marginBottom: 0 }} role="group" aria-label="Date range">
          {RANGES.map((r) => <button key={r} className={days === r ? "active" : ""} aria-pressed={days === r} onClick={() => setDays(r)}>Last {r} days</button>)}
        </div>
      </div>

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat variant="feature" label="Calls" value={t.calls.toLocaleString()}
          hint={`${t.inbound_calls.toLocaleString()} inbound · ${t.campaign_calls.toLocaleString()} campaign`} />
        <Stat label="Minutes on calls" value={t.call_minutes.toLocaleString()} hint={t.calls ? `${(t.call_minutes / t.calls).toFixed(1)} minutes per call` : "no calls in this period"} />
        <Stat label="Used" value={money(t.usage_amount, cur)} hint="calls and messages charged, before fees and tax" />
        <Stat label="Invoiced" value={money(t.invoiced, cur)} hint={t.amount_due > 0 ? `${money(t.amount_due, cur)} unpaid right now` : "nothing unpaid right now"} />
        <Stat label="Tickets created" value={t.tickets_created.toLocaleString()} hint={`${t.conversations.toLocaleString()} conversations`} />
        <Stat label="Resolved by AI" value={percent(t.ai_resolved_rate)}
          hint={t.conversations_finished ? `${t.resolved_by_ai} of ${t.conversations_finished} finished conversations, with no ticket` : "no finished conversations yet"} />
        <Stat label="Companies with activity" value={t.active_companies} hint={`of ${data.companies.length}`} />
        <Stat label="New companies" value={t.new_companies} hint="registered in this period" />
      </div>
      {data.other_currencies.length > 0 && (
        <p className="muted small">Money totals are in {cur}. Companies billed in {data.other_currencies.join(", ")} keep their own figures in the table below and are not added in.</p>
      )}

      <div className="card">
        <div className="section-title">
          <div>
            <h2>Calls per day</h2>
            <div className="muted small">Calls customers made to companies, and calls campaigns made to customers</div>
          </div>
          <Legend names={["Inbound", "Campaign"]} />
        </div>
        <TwoLines names={["Inbound", "Campaign"]} label="Line chart of inbound and campaign calls per day, all companies."
          points={data.daily.map((d) => ({ key: d.date, axis: data.daily.length <= 7 ? weekday(d.date) : shortDay(d.date), title: shortDay(d.date), one: d.inbound_calls, two: d.campaign_calls }))}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Day</th><th className="num">Inbound calls</th><th className="num">Campaign calls</th><th className="num">Tickets</th><th className="num">Conversations</th></tr></thead>
              <tbody>
                {[...data.daily].reverse().map((d) => (
                  <tr key={d.date}><td>{shortDay(d.date)}</td><td className="num">{d.inbound_calls}</td><td className="num">{d.campaign_calls}</td><td className="num">{d.tickets}</td><td className="num">{d.conversations}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </TwoLines>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="section-title">
            <div>
              <h2>Money per month</h2>
              <div className="muted small">Last 6 months, in {cur}: what was invoiced, and what was paid in that month</div>
            </div>
            {hasMoney && <Legend boxes names={["Invoiced", "Paid"]} />}
          </div>
          {!hasMoney ? <p className="muted" style={{ margin: 0 }}>No invoices in the last 6 months.</p> : (
            <Columns names={["Invoiced", "Paid"]} label="Column chart of money invoiced and paid per month." format={(v) => money(v, cur)} tick={compact}
              points={data.months.map((m) => ({ key: m.month, axis: monthName(m.month), title: monthName(m.month, true), one: m.invoiced, two: m.paid }))}>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Month</th><th className="num">Invoiced</th><th className="num">Paid</th></tr></thead>
                  <tbody>
                    {[...data.months].reverse().map((m) => (
                      <tr key={m.month}><td>{monthName(m.month, true)}</td><td className="num">{money(m.invoiced, cur)}</td><td className="num">{money(m.paid, cur)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Columns>
          )}
        </div>
        <div className="card">
          <h2>By channel</h2>
          <Bars unit="conversations" empty="No conversations in this period."
            rows={data.channels.map((c) => ({
              label: CHANNEL[c.channel] ?? c.channel, value: c.conversations,
              note: c.conversations ? `${c.resolved_by_ai} resolved by AI with no ticket` : undefined,
            }))} />
        </div>
      </div>

      <div className="card">
        <div className="section-title">
          <div>
            <h2>By company</h2>
            <div className="muted small">Click a column to sort. Click a company to open it.</div>
          </div>
        </div>
        {!rows.length ? <Empty>No companies yet.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr>{head("name", "Company", false)}{COLUMNS.map((c) => head(c.key, c.label))}</tr></thead>
              <tbody>
                {rows.map((c: Row) => (
                  <tr key={c.company_id} className="clickable" onClick={() => navigate(`/companies/${c.company_id}`)}>
                    <td><strong>{c.name}</strong>{!c.is_active && <> <Badge value="suspended" /></>}</td>
                    <td className="num">{c.calls.toLocaleString()}<div className="small muted">{c.inbound_calls.toLocaleString()} in · {c.campaign_calls.toLocaleString()} out</div></td>
                    <td className="num">{c.call_minutes.toLocaleString()}</td>
                    <td className="num">{money(c.average_cost_per_call, c.currency ?? cur)}</td>
                    <td className="num">{c.currency ? money(c.usage_amount, c.currency) : <span className="muted small">not billed</span>}</td>
                    <td className="num">{c.tickets.toLocaleString()}</td>
                    <td className="num">{percent(c.ai_resolved_rate)}</td>
                    <td className="num">{c.invoiced ? money(c.invoiced, c.currency ?? cur) : "—"}</td>
                    <td className="num">{c.amount_due > 0 ? <strong>{money(c.amount_due, c.currency ?? cur)}</strong> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
