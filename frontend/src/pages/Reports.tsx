import { useEffect, useRef, useState, type PointerEvent } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ReportOverview } from "../api/types";
import { ErrorBox, Loading, PageHeader, Stat } from "../components/ui";

type Day = ReportOverview["daily"][number];
const RANGES = [7, 30, 90] as const;
const CHANNEL: Record<string, string> = { whatsapp: "WhatsApp", phone: "Phone calls", email: "Email" };
const OUTCOME: Record<string, string> = {
  answered: "Answered by AI", ticket_raised: "Ticket raised", ticket_updated: "Added to a ticket",
  callback_booked: "Callback booked", abandoned: "Hung up early", in_progress: "On the line now",
};
const shortDay = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
const weekday = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: "short" });

/** The element's current width, so a chart is drawn at real pixels and its text never stretches. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** A "nice" top for the y axis that divides into four whole steps: 0 -> 4, 7 -> 8, 13 -> 16, 130 -> 160. */
function niceMax(max: number) {
  if (max <= 4) return 4;
  const step = 10 ** Math.floor(Math.log10(max));
  const nice = [1, 1.2, 1.6, 2, 2.4, 3.2, 4, 6, 8, 10].map((m) => m * step).find((v) => v >= max) ?? max;
  return Math.ceil(nice / 4) * 4; // four equal steps, so every grid line is a whole number
}

/** Tickets created and resolved per day: two lines, a crosshair that reads out both, and the numbers as a table. */
function TicketsChart({ days }: { days: Day[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [at, setAt] = useState<number | null>(null);
  const H = 240, left = 34, right = 14, top = 12, bottom = 28;
  const w = Math.max(0, width - left - right), h = H - top - bottom;
  const max = niceMax(Math.max(...days.map((d) => Math.max(d.tickets_created, d.tickets_resolved))));
  const x = (i: number) => left + (days.length > 1 ? (i * w) / (days.length - 1) : w / 2);
  const y = (v: number) => top + h - (v / max) * h;
  const line = (key: "tickets_created" | "tickets_resolved") => days.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join(" ");
  const every = Math.max(1, Math.ceil(days.length / Math.max(2, Math.floor(w / 64))));
  const move = (e: PointerEvent<SVGSVGElement>) => {
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
    setAt(Math.min(days.length - 1, Math.max(0, Math.round(((px - left) / Math.max(1, w)) * (days.length - 1)))));
  };
  const day = at === null ? null : days[at];

  return (
    <div className="chart" ref={ref}>
      {width > 0 && (
        <svg width={width} height={H} role="img" aria-label="Line chart of tickets created and resolved per day. The same numbers are in the table below."
          onPointerMove={move} onPointerLeave={() => setAt(null)}>
          {[0, 0.25, 0.5, 0.75, 1].map((f) => (
            <g key={f}>
              <line x1={left} x2={left + w} y1={y(max * f)} y2={y(max * f)} className="chart-grid" />
              <text x={left - 8} y={y(max * f) + 4} textAnchor="end" className="chart-tick">{Math.round(max * f)}</text>
            </g>
          ))}
          {days.map((d, i) => (i % every === 0 || i === days.length - 1) && (days.length - 1 - i >= every / 2 || i === days.length - 1) && (
            <text key={d.date} x={x(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === days.length - 1 ? "end" : "middle"} className="chart-tick">
              {days.length <= 7 ? weekday(d.date) : shortDay(d.date)}
            </text>
          ))}
          {at !== null && <line x1={x(at)} x2={x(at)} y1={top} y2={top + h} className="chart-cross" />}
          <path d={line("tickets_resolved")} className="chart-line two" />
          <path d={line("tickets_created")} className="chart-line one" />
          {at !== null && day && (
            <>
              <circle cx={x(at)} cy={y(day.tickets_resolved)} r={5} className="chart-dot two" />
              <circle cx={x(at)} cy={y(day.tickets_created)} r={5} className="chart-dot one" />
            </>
          )}
        </svg>
      )}
      {day && at !== null && (
        <div className="chart-tip" style={{ left: Math.min(Math.max(x(at), 90), Math.max(90, width - 90)) }} role="status">
          <div className="chart-tip-title">{shortDay(day.date)}</div>
          <div><span className="key one" /> <strong>{day.tickets_created}</strong> created</div>
          <div><span className="key two" /> <strong>{day.tickets_resolved}</strong> resolved</div>
        </div>
      )}
      <details className="chart-table">
        <summary>Show the numbers</summary>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Day</th><th>Tickets created</th><th>Tickets resolved</th><th>Conversations</th><th>Inbound calls</th></tr></thead>
            <tbody>
              {[...days].reverse().map((d) => (
                <tr key={d.date}><td>{shortDay(d.date)}</td><td>{d.tickets_created}</td><td>{d.tickets_resolved}</td><td>{d.conversations}</td><td>{d.inbound_calls}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

/** Horizontal bars for a handful of named amounts. Every value is written out, so nothing depends on hovering. */
function Bars({ rows, unit, empty }: { rows: { label: string; value: number; note?: string }[]; unit: string; empty: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.some((r) => r.value)) return <p className="muted" style={{ margin: 0 }}>{empty}</p>;
  return (
    <div className="bars" role="list">
      {rows.map((r) => (
        <div className="bar-row" role="listitem" key={r.label} title={`${r.label}: ${r.value} ${unit}${r.note ? ` · ${r.note}` : ""}`}>
          <div className="bar-head">
            <span>{r.label}</span>
            <strong>{r.value.toLocaleString()}</strong>
          </div>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${(100 * r.value) / max}%` }} /></div>
          {r.note && <div className="small muted">{r.note}</div>}
        </div>
      ))}
    </div>
  );
}

export default function Reports() {
  const [days, setDays] = useState<(typeof RANGES)[number]>(7);
  const { data, isLoading, error } = useQuery({
    queryKey: ["reports", days],
    queryFn: async () => (await api.get<ReportOverview>("/reports/overview", { params: { days } })).data,
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });

  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const t = data.totals;
  const rate = t.ai_resolved_rate === null ? "—" : `${Math.round(t.ai_resolved_rate * 100)}%`;
  const hours = t.avg_resolution_hours;
  const speed = hours === null ? "—" : hours < 1 ? `${Math.round(hours * 60)} min` : hours < 48 ? `${hours} h` : `${Math.round(hours / 24)} days`;

  return (
    <>
      <PageHeader title="Reports" subtitle={`${shortDay(data.from)} to ${shortDay(data.to)}, in your company's time (${data.timezone}).`} />
      <div className="filters">
        <div className="tabs" style={{ marginBottom: 0 }} role="group" aria-label="Date range">
          {RANGES.map((r) => <button key={r} className={days === r ? "active" : ""} aria-pressed={days === r} onClick={() => setDays(r)}>Last {r} days</button>)}
        </div>
      </div>

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat variant="feature" label="Resolved by AI" value={rate}
          hint={t.conversations_finished ? `${t.resolved_by_ai} of ${t.conversations_finished} finished conversations, with no ticket` : "no finished conversations yet"} />
        <Stat label="Tickets created" value={t.tickets_created} hint={`${t.tickets_open} open right now`} />
        <Stat label="Tickets resolved" value={t.tickets_resolved} hint={hours === null ? "none resolved in this period" : `in ${speed} on average`} />
        <Stat label="Conversations" value={t.conversations} hint={`${t.inbound_calls} of them phone calls`} />
      </div>

      <div className="card">
        <div className="section-title">
          <div>
            <h2>Tickets per day</h2>
            <div className="muted small">How many were raised and how many your team resolved</div>
          </div>
          <div className="legend">
            <span><i className="key one" /> Created</span>
            <span><i className="key two" /> Resolved</span>
          </div>
        </div>
        <TicketsChart days={data.daily} />
      </div>

      <div className="grid grid-2">
        <div className="card">
          <h2>By channel</h2>
          <Bars unit="conversations" empty="No conversations in this period."
            rows={data.channels.map((c) => ({
              label: CHANNEL[c.channel] ?? c.channel, value: c.conversations,
              note: c.conversations ? `${c.resolved_by_ai} resolved by AI · ${c.tickets} ${c.tickets === 1 ? "ticket" : "tickets"} raised` : undefined,
            }))} />
        </div>
        <div className="card">
          <h2>How inbound calls ended</h2>
          <Bars unit="calls" empty="No inbound calls in this period."
            rows={data.call_outcomes.map((o) => ({ label: OUTCOME[o.outcome] ?? o.outcome.replace(/_/g, " "), value: o.count }))} />
        </div>
        <div className="card">
          <h2>Tickets resolved per person</h2>
          <Bars unit="tickets" empty="No tickets were resolved in this period." rows={data.agents.map((a) => ({ label: a.name, value: a.resolved }))} />
        </div>
        <div className="card">
          <h2>Also in this period</h2>
          <dl className="meta-list">
            <dt>Campaign calls made</dt><dd>{t.campaign_calls.toLocaleString()} <span className="muted">· {t.campaign_calls_completed.toLocaleString()} completed</span></dd>
            <dt>Callbacks booked</dt><dd>{t.callbacks_booked} <span className="muted">· {t.callbacks_completed} done</span></dd>
            <dt>Unanswered questions</dt><dd>{t.open_gaps} <span className="muted">waiting under Knowledge Base</span></dd>
          </dl>
        </div>
      </div>
    </>
  );
}
