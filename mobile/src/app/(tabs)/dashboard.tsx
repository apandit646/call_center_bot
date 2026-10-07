import { StyleSheet, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { DashboardSummary } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Card, ErrorBox, H2, Loading, Notice, Row, Screen, Stat, T, UsageMeter } from "@/components/ui";
import { useCompany } from "@/hooks";

export default function Dashboard() {
  const { user } = useAuth();
  const { data, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: ["dashboard"],
    queryFn: async () => (await api.get<DashboardSummary>("/dashboard/summary")).data,
    refetchInterval: 30_000,
  });

  const { data: company } = useCompany();

  if (isLoading) return <Loading />;
  return (
    <Screen refreshing={isRefetching} onRefresh={refetch}>
      <View>
        <T bold style={s.company}>{user?.company?.name}</T>
        <T muted small>Signed in as {user?.full_name}</T>
      </View>
      <ErrorBox error={error} />
      {company?.billing_hold && (
        <Notice tone="red">
          An invoice is overdue, so campaigns and callbacks are on hold. Inbound calls, WhatsApp and email still work.
          {user?.role === "admin" ? " Pay it under More, Billing." : ""}
        </Notice>
      )}
      {data && (
        <>
          <View style={s.grid}>
            <Stat label="Call-backs due today" value={data.callbacks.due_today} tone={data.callbacks.overdue ? "red" : "grey"}
              hint={data.callbacks.overdue ? `${data.callbacks.overdue} overdue: see More, Callbacks` : "none are late"} />
          </View>
          <View>
            <H2>Tickets</H2>
            <View style={s.grid}>
              <Stat feature label="Open" value={data.tickets.open} hint="across WhatsApp and calls" />
              <Stat label="Unassigned" value={data.tickets.unassigned} tone={data.tickets.unassigned ? "amber" : "green"} />
              <Stat label="Created today" value={data.tickets.created_today} tone="violet" />
              <Stat label="Urgent, open" value={data.tickets.open_by_priority.urgent ?? 0} tone={data.tickets.open_by_priority.urgent ? "red" : "grey"} />
            </View>
          </View>

          <View>
            <H2>WhatsApp today</H2>
            <View style={s.grid}>
              <Stat label="Conversations" value={data.whatsapp.conversations_today} />
              <Stat label="Open now" value={data.whatsapp.open_conversations} tone="amber" />
              <Stat label="Resolved by AI" value={data.whatsapp.resolved_by_ai_today} tone="green" />
              <Stat label="Escalated" value={data.whatsapp.escalated_today} tone={data.whatsapp.escalated_today ? "red" : "grey"} />
            </View>
          </View>

          <View>
            <Row spread>
              <H2>Inbound calls today</H2>
              <Badge value={data.inbound.active ? "on" : "off"} />
            </Row>
            <View style={s.grid}>
              <Stat label="Calls" value={data.inbound.calls_today} />
              <Stat label="On the line now" value={data.inbound.active_calls} tone="amber" />
              <Stat label="Answered by AI" value={data.inbound.answered_by_ai_today} tone="green" />
              <Stat label="Tickets raised" value={data.inbound.tickets_today} tone="violet" />
            </View>
          </View>

          <View>
            <H2>Campaign calls today</H2>
            <View style={s.grid}>
              <Stat label="Running campaigns" value={data.outbound.running_campaigns} tone="green" />
              <Stat label="On the line now" value={data.outbound.active_calls} tone="amber" />
              <Stat label="Calls made" value={data.outbound.calls_today} />
              <Stat label="Completed" value={data.outbound.completed_calls_today} tone="green" />
            </View>
          </View>

          <Card>
            <H2>Usage against your plan</H2>
            <UsageMeter label="Campaign calls today" used={data.usage.calls_today} limit={data.usage.daily_call_limit} />
            <UsageMeter label="Campaigns" used={data.usage.campaigns} limit={data.usage.max_campaigns} />
            <UsageMeter label="Calls at the same time" used={data.usage.active_calls} limit={data.usage.max_concurrent_calls} />
          </Card>
        </>
      )}
    </Screen>
  );
}

const s = StyleSheet.create({
  company: { fontSize: 24, fontWeight: "800", letterSpacing: -0.4, lineHeight: 30 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
});
