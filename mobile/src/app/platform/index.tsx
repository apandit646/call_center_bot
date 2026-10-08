import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Company, PlatformDashboard } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Button, Card, ErrorBox, H2, Input, ListRow, PagedList, Row, Stat, T, money, type Tone } from "@/components/ui";
import { useDebounced } from "@/hooks";

type Item = PlatformDashboard["attention"][number];
const LEVEL: Record<Item["level"], { label: string; tone: Tone }> = {
  high: { label: "act now", tone: "red" }, medium: { label: "soon", tone: "amber" }, low: { label: "to know", tone: "grey" },
};
const limit = (used: number, max: number | null) => `${used.toLocaleString()} / ${max === null ? "∞" : max.toLocaleString()}`;

/** The platform owner's app: today across every company, what needs them, and every company. */
export default function Companies() {
  const router = useRouter();
  const { user } = useAuth();
  const [q, setQ] = useState("");
  const search = useDebounced(q);
  const dashboard = useQuery({
    queryKey: ["platform-dashboard"],
    queryFn: async () => (await api.get<PlatformDashboard>("/platform/dashboard")).data,
    refetchInterval: 30_000,
    enabled: user?.role === "super_admin",
  });

  if (!user) return <Redirect href="/login" />;
  if (user.role !== "super_admin") return <Redirect href="/dashboard" />;
  const d = dashboard.data, t = d?.totals;

  return (
    <>
      <Stack.Screen options={{ headerRight: () => <Button small variant="ghost" title="Account" onPress={() => router.push("/account")} /> }} />
      <PagedList<Company>
        queryKey={["companies"]}
        url="/companies"
        params={{ q: search }}
        refetchInterval={30_000}
        empty="No companies match this search. Companies are registered on the web portal."
        header={<Input placeholder="Search company name or email…" value={q} onChangeText={setQ} autoCorrect={false} returnKeyType="search" />}
        top={
          <>
            <ErrorBox error={dashboard.error} />
            <Button variant="ghost" onPress={() => router.push("/issues")}
              title={dashboard.data?.totals.open_issues ? `Issue reports from companies (${dashboard.data.totals.open_issues} open)` : "Issue reports from companies"} />
            {d && t && (
              <>
                <View style={st.grid}>
                  <Stat feature label="Calls today" value={t.calls_today.toLocaleString()}
                    hint={`${t.inbound_calls_today} inbound · ${t.campaign_calls_today} campaign`} />
                  <Stat label="On the line now" value={t.live_calls} tone={t.live_calls ? "green" : "grey"}
                    hint={`${t.running_campaigns} ${t.running_campaigns === 1 ? "campaign" : "campaigns"} running`} />
                  <Stat label="Active companies" value={t.active_companies} tone="violet" hint={`of ${t.companies} · ${t.users} users`} />
                  <Stat label="Tickets today" value={t.tickets_today} tone="grey" hint={`${t.conversations_today} conversations`} />
                  <Stat label="Waiting to be paid" value={money(t.amount_due, d.currency)} tone={t.amount_overdue > 0 ? "red" : "grey"}
                    hint={t.amount_overdue > 0 ? `${money(t.amount_overdue, d.currency)} overdue` : "none overdue"} />
                  <Stat label="Open issue reports" value={t.open_issues} tone={t.open_issues ? "amber" : "grey"} />
                </View>
                <Card>
                  <H2>Needs your attention</H2>
                  {!d.attention.length && <T muted>Nothing needs you right now.</T>}
                  {d.attention.map((a) => (
                    <ListRow key={`${a.company_id}-${a.kind}`}
                      onPress={() => router.push(a.target === "issues" ? "/issues" : `/platform/${a.company_id}`)}>
                      <Row spread>
                        <T bold style={st.name}>{a.company}</T>
                        <Badge value={LEVEL[a.level].label} tone={LEVEL[a.level].tone} />
                      </Row>
                      <T small>{a.text}</T>
                    </ListRow>
                  ))}
                </Card>
                <T small muted>Full reports with charts and the per-company table are on the web portal, under Reports.</T>
              </>
            )}
            <H2>Companies</H2>
          </>
        }
        renderItem={(c) => (
          <ListRow onPress={() => router.push(`/platform/${c.id}`)}>
            <Row spread>
              <T bold style={st.name}>{c.name}</T>
              <Badge value={c.is_active ? "active" : "suspended"} />
            </Row>
            {c.email && <T small muted>{c.email}</T>}
            {c.usage && (
              <T small muted>
                Campaigns {limit(c.usage.campaigns, c.usage.max_campaigns)} · Calls today {limit(c.usage.calls_today, c.usage.daily_call_limit)} · {c.usage.users} users
              </T>
            )}
          </ListRow>
        )}
      />
    </>
  );
}

const st = StyleSheet.create({
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  name: { flexShrink: 1 },
});
