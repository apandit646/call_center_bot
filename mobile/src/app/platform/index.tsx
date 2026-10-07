import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Redirect, Stack, useRouter } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Company, PlatformSummary } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Button, ErrorBox, Input, ListRow, PagedList, Row, Stat, T } from "@/components/ui";
import { useDebounced } from "@/hooks";
import { useIssueSummary } from "@/issues";

const limit = (used: number, max: number | null) => `${used.toLocaleString()} / ${max === null ? "∞" : max.toLocaleString()}`;

/** The platform owner's app: every company, and how much each is using. */
export default function Companies() {
  const router = useRouter();
  const { user } = useAuth();
  const [q, setQ] = useState("");
  const search = useDebounced(q);
  const { data: issues } = useIssueSummary(user?.role === "super_admin");
  const summary = useQuery({
    queryKey: ["platform-summary"],
    queryFn: async () => (await api.get<PlatformSummary>("/platform/summary")).data,
    refetchInterval: 30_000,
    enabled: user?.role === "super_admin",
  });

  if (!user) return <Redirect href="/login" />;
  if (user.role !== "super_admin") return <Redirect href="/dashboard" />;
  const s = summary.data;

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
            <ErrorBox error={summary.error} />
            <Button variant="ghost" onPress={() => router.push("/issues")}
              title={issues?.inbox_open ? `Issue reports from companies (${issues.inbox_open} open)` : "Issue reports from companies"} />
            {s && (
              <View style={st.grid}>
                <Stat label="Companies" value={s.companies} hint={`${s.active_companies} active`} />
                <Stat label="Users" value={s.users} tone="violet" />
                <Stat label="Running campaigns" value={s.running_campaigns} hint={`${s.campaigns} in total`} tone="green" />
                <Stat label="Calls, last 24 hours" value={s.calls_last_24h} hint={`${s.active_calls} on the line now`} tone="amber" />
              </View>
            )}
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
