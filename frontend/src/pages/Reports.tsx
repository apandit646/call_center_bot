import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ReportOverview } from "../api/types";
import { Bars, Legend, TwoLines, shortDay, weekday } from "../components/Charts";
import { ErrorBox, Loading, PageHeader, Stat } from "../components/ui";

const RANGES = [7, 30, 90] as const;
const CHANNEL: Record<string, string> = { whatsapp: "WhatsApp", phone: "Phone calls", email: "Email" };
const OUTCOME: Record<string, string> = {
  answered: "Answered by AI", ticket_raised: "Ticket raised", ticket_updated: "Added to a ticket",
  callback_booked: "Callback booked", abandoned: "Hung up early", in_progress: "On the line now",
};

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
          <Legend names={["Created", "Resolved"]} />
        </div>
        <TwoLines names={["Created", "Resolved"]} label="Line chart of tickets created and resolved per day."
          points={data.daily.map((d) => ({ key: d.date, axis: data.daily.length <= 7 ? weekday(d.date) : shortDay(d.date), title: shortDay(d.date), one: d.tickets_created, two: d.tickets_resolved }))}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Day</th><th>Tickets created</th><th>Tickets resolved</th><th>Conversations</th><th>Inbound calls</th></tr></thead>
              <tbody>
                {[...data.daily].reverse().map((d) => (
                  <tr key={d.date}><td>{shortDay(d.date)}</td><td>{d.tickets_created}</td><td>{d.tickets_resolved}</td><td>{d.conversations}</td><td>{d.inbound_calls}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </TwoLines>
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
