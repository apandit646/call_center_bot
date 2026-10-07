import { useState } from "react";
import { StyleSheet, View, useColorScheme } from "react-native";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { ReportOverview } from "@/api/types";
import { Card, Chips, ErrorBox, H2, KV, Loading, Row, Screen, Stat, T } from "@/components/ui";
import { useColors } from "@/theme";

const RANGES = [["7", "7 days"], ["30", "30 days"], ["90", "90 days"]] as const;
const CHANNEL: Record<string, string> = { whatsapp: "WhatsApp", phone: "Phone calls", email: "Email" };
const OUTCOME: Record<string, string> = {
  answered: "Answered by AI", ticket_raised: "Ticket raised", ticket_updated: "Added to a ticket", callback_booked: "Callback booked",
  abandoned: "Hung up early", in_progress: "On the line now",
};
// The two series of the tickets chart; the same pair as the web portal (checked for colour-blind separation).
const SERIES = { created: { light: "#5d4df5", dark: "#7b6ef7" }, resolved: { light: "#0f8a6c", dark: "#1f9d7e" } };
const shortDay = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** Horizontal bars for a few named amounts; every value is written out. */
function Bars({ rows, empty, color }: { rows: { label: string; value: number; note?: string }[]; empty: string; color: string }) {
  const c = useColors();
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.some((r) => r.value)) return <T muted>{empty}</T>;
  return (
    <View style={s.bars}>
      {rows.map((r) => (
        <View key={r.label} accessible accessibilityLabel={`${r.label}: ${r.value}${r.note ? `, ${r.note}` : ""}`}>
          <Row spread>
            <T>{r.label}</T>
            <T bold>{r.value.toLocaleString()}</T>
          </Row>
          <View style={[s.track, { backgroundColor: c.panel2 }]}>
            <View style={[s.fill, { width: `${(100 * r.value) / max}%`, backgroundColor: color }]} />
          </View>
          {r.note && <T small muted>{r.note}</T>}
        </View>
      ))}
    </View>
  );
}

/** Tickets created and resolved per day, as pairs of thin columns. For long ranges the days are grouped into weeks. */
function TicketColumns({ days, created, resolved }: { days: ReportOverview["daily"]; created: string; resolved: string }) {
  const c = useColors();
  const size = days.length > 14 ? 7 : 1;
  const groups = [];
  for (let i = 0; i < days.length; i += size) {
    const part = days.slice(i, i + size);
    groups.push({
      label: shortDay(part[0].date),
      created: part.reduce((n, d) => n + d.tickets_created, 0),
      resolved: part.reduce((n, d) => n + d.tickets_resolved, 0),
    });
  }
  const max = Math.max(1, ...groups.map((g) => Math.max(g.created, g.resolved)));
  const every = Math.max(1, Math.ceil(groups.length / 5));
  return (
    <View>
      <Row>
        <View style={[s.key, { backgroundColor: created }]} /><T small>Created</T>
        <View style={[s.key, { backgroundColor: resolved }]} /><T small>Resolved</T>
        <T small muted>· highest {max}{size > 1 ? " per week" : " per day"}</T>
      </Row>
      <View style={[s.plot, { borderBottomColor: c.borderStrong }]}>
        {groups.map((g) => (
          <View key={g.label} style={s.group} accessible accessibilityLabel={`${g.label}: ${g.created} created, ${g.resolved} resolved`}>
            <View style={[s.column, { height: `${(100 * g.created) / max}%`, backgroundColor: created }]} />
            <View style={[s.column, { height: `${(100 * g.resolved) / max}%`, backgroundColor: resolved }]} />
          </View>
        ))}
      </View>
      <View style={s.axis}>
        {groups.map((g, i) => <T key={g.label} small muted style={s.tick}>{i % every === 0 ? g.label : ""}</T>)}
      </View>
    </View>
  );
}

export default function Reports() {
  const dark = useColorScheme() === "dark";
  const [range, setRange] = useState<(typeof RANGES)[number][0]>("7");
  const { data, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: ["reports", range],
    queryFn: async () => (await api.get<ReportOverview>("/reports/overview", { params: { days: Number(range) } })).data,
    placeholderData: keepPreviousData,
  });
  if (isLoading) return <Loading />;
  if (error || !data) return <Screen><ErrorBox error={error ?? new Error("Reports are not available")} /></Screen>;
  const t = data.totals;
  const created = dark ? SERIES.created.dark : SERIES.created.light;
  const resolved = dark ? SERIES.resolved.dark : SERIES.resolved.light;
  const hours = t.avg_resolution_hours;
  const speed = hours === null ? null : hours < 1 ? `${Math.round(hours * 60)} min` : hours < 48 ? `${hours} h` : `${Math.round(hours / 24)} days`;

  return (
    <Screen refreshing={isRefetching} onRefresh={refetch}>
      <Chips options={RANGES} value={range} onChange={setRange} />
      <T small muted>{shortDay(data.from)} to {shortDay(data.to)} ({data.timezone})</T>
      <View style={s.grid}>
        <Stat feature label="Resolved by AI" value={t.ai_resolved_rate === null ? "—" : `${Math.round(t.ai_resolved_rate * 100)}%`}
          hint={t.conversations_finished ? `${t.resolved_by_ai} of ${t.conversations_finished} conversations` : "no finished conversations"} />
        <Stat label="Tickets created" value={t.tickets_created} hint={`${t.tickets_open} open now`} />
        <Stat label="Tickets resolved" value={t.tickets_resolved} tone="green" hint={speed ? `in ${speed} on average` : "none in this period"} />
        <Stat label="Conversations" value={t.conversations} tone="violet" hint={`${t.inbound_calls} phone calls`} />
      </View>

      <Card>
        <H2>Tickets per {Number(range) > 14 ? "week" : "day"}</H2>
        <TicketColumns days={data.daily} created={created} resolved={resolved} />
      </Card>
      <Card>
        <H2>By channel</H2>
        <Bars color={created} empty="No conversations in this period."
          rows={data.channels.map((ch) => ({
            label: CHANNEL[ch.channel] ?? ch.channel, value: ch.conversations,
            note: ch.conversations ? `${ch.resolved_by_ai} resolved by AI · ${ch.tickets} tickets raised` : undefined,
          }))} />
      </Card>
      <Card>
        <H2>How inbound calls ended</H2>
        <Bars color={created} empty="No inbound calls in this period."
          rows={data.call_outcomes.map((o) => ({ label: OUTCOME[o.outcome] ?? o.outcome.replace(/_/g, " "), value: o.count }))} />
      </Card>
      <Card>
        <H2>Tickets resolved per person</H2>
        <Bars color={created} empty="No tickets were resolved in this period." rows={data.agents.map((a) => ({ label: a.name, value: a.resolved }))} />
      </Card>
      <Card>
        <H2>Also in this period</H2>
        <KV k="Campaign calls">{`${t.campaign_calls} · ${t.campaign_calls_completed} completed`}</KV>
        <KV k="Callbacks">{`${t.callbacks_booked} booked · ${t.callbacks_completed} done`}</KV>
        <KV k="Unanswered">{`${t.open_gaps} questions waiting`}</KV>
      </Card>
    </Screen>
  );
}

const s = StyleSheet.create({
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  bars: { gap: 14 },
  track: { height: 10, borderRadius: 4, overflow: "hidden", marginTop: 6 },
  fill: { height: 10, minWidth: 2, borderTopRightRadius: 4, borderBottomRightRadius: 4 },
  key: { width: 12, height: 12, borderRadius: 3 },
  plot: { height: 150, flexDirection: "row", alignItems: "flex-end", gap: 6, borderBottomWidth: 1, marginTop: 12 },
  group: { flex: 1, height: "100%", flexDirection: "row", alignItems: "flex-end", justifyContent: "center", gap: 2 },
  column: { flex: 1, maxWidth: 14, minHeight: 2, borderTopLeftRadius: 4, borderTopRightRadius: 4 },
  axis: { flexDirection: "row", gap: 6, marginTop: 4 },
  tick: { flex: 1, textAlign: "center", fontSize: 10.5 },
});
