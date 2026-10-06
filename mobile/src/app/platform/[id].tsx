import { useState } from "react";
import { Alert, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Company } from "@/api/types";
import { Badge, Button, Card, ErrorBox, H2, Input, KV, Loading, Notice, Row, Screen, T, UsageMeter, fmtDate } from "@/components/ui";

/** "" = unlimited (null); otherwise a whole number. undefined = not a valid number. */
function parseLimit(text: string): number | null | undefined {
  const t = text.trim();
  if (!t) return null;
  return /^\d+$/.test(t) ? Number(t) : undefined;
}
const show = (v: number | null) => (v === null ? "" : String(v));

function Limits({ company, saving, saved, onSave }: {
  company: Company; saving: boolean; saved: boolean; onSave: (limits: Record<string, number | null>) => void;
}) {
  const [campaigns, setCampaigns] = useState(show(company.max_campaigns));
  const [daily, setDaily] = useState(show(company.daily_call_limit));
  const [concurrent, setConcurrent] = useState(show(company.max_concurrent_calls));

  const max_campaigns = parseLimit(campaigns);
  const daily_call_limit = parseLimit(daily);
  const max_concurrent_calls = parseLimit(concurrent);
  const valid = max_campaigns !== undefined && daily_call_limit !== undefined && max_concurrent_calls !== undefined && max_concurrent_calls !== 0;
  const changed =
    max_campaigns !== company.max_campaigns || daily_call_limit !== company.daily_call_limit || max_concurrent_calls !== company.max_concurrent_calls;

  return (
    <Card style={s.form}>
      <H2>Plan limits</H2>
      <T small muted>Leave a box empty for unlimited.</T>
      <Input label="Campaigns" value={campaigns} onChangeText={setCampaigns} keyboardType="number-pad" placeholder="Unlimited" />
      <Input label="Campaign calls per day" value={daily} onChangeText={setDaily} keyboardType="number-pad" placeholder="Unlimited" />
      <Input label="Calls at the same time" value={concurrent} onChangeText={setConcurrent} keyboardType="number-pad" placeholder="Unlimited" />
      {!valid && <T small muted>Use whole numbers only; calls at the same time must be at least 1.</T>}
      {saved && !changed && <Notice tone="green">Saved.</Notice>}
      <Button
        title="Save limits" busy={saving} disabled={!valid || !changed}
        onPress={() => valid && onSave({ max_campaigns, daily_call_limit, max_concurrent_calls })}
      />
    </Card>
  );
}

/** One company, for the platform owner: its plan limits, usage and what it has connected. */
export default function CompanyDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const qc = useQueryClient();

  const { data: company, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: ["company", id],
    queryFn: async () => (await api.get<Company>(`/companies/${id}`)).data,
  });

  const save = useMutation({
    mutationFn: async (body: Record<string, unknown>) => (await api.patch<Company>(`/companies/${id}`, body)).data,
    onSuccess: (data) => {
      qc.setQueryData(["company", id], data);
      qc.invalidateQueries({ queryKey: ["companies"] });
      qc.invalidateQueries({ queryKey: ["platform-summary"] });
    },
  });

  if (isLoading) return <Loading />;
  if (error || !company) return <Screen><ErrorBox error={error ?? new Error("Company not found")} /></Screen>;

  const confirmSuspend = () =>
    Alert.alert(`Suspend ${company.name}?`, "Its users can't sign in and its campaigns stop calling.", [
      { text: "Cancel", style: "cancel" },
      { text: "Suspend", style: "destructive", onPress: () => save.mutate({ is_active: false }) },
    ]);
  const u = company.usage;

  return (
    <Screen refreshing={isRefetching} onRefresh={refetch}>
      <Stack.Screen options={{ title: company.name }} />
      <ErrorBox error={save.error} />

      <Card>
        <Row spread>
          <T bold style={s.name}>{company.name}</T>
          <Badge value={company.is_active ? "active" : "suspended"} />
        </Row>
        {company.industry && <KV k="Industry">{company.industry}</KV>}
        {company.email && <KV k="Email">{company.email}</KV>}
        {company.phone && <KV k="Phone">{company.phone}</KV>}
        {company.address && <KV k="Address">{company.address}</KV>}
        <KV k="Registered">{fmtDate(company.created_at)}</KV>
        <Row>
          {company.is_active
            ? <Button small variant="danger" title="Suspend company" disabled={save.isPending} onPress={confirmSuspend} />
            : <Button small title="Re-activate" busy={save.isPending} onPress={() => save.mutate({ is_active: true })} />}
        </Row>
      </Card>

      {u && (
        <Card>
          <H2>Usage</H2>
          <UsageMeter label="Campaign calls today" used={u.calls_today} limit={u.daily_call_limit} />
          <UsageMeter label="Campaigns" used={u.campaigns} limit={u.max_campaigns} />
          <UsageMeter label="Calls at the same time" used={u.active_calls} limit={u.max_concurrent_calls} />
          <T small muted>{u.users} users · {u.inbound_calls_today} inbound calls today</T>
        </Card>
      )}

      {/* The key refills the boxes from the saved values after a save or a refresh. */}
      <Limits
        key={`${company.max_campaigns}/${company.daily_call_limit}/${company.max_concurrent_calls}`}
        company={company}
        saving={save.isPending}
        saved={save.isSuccess}
        onSave={(limits) => save.mutate(limits)}
      />

      <Card>
        <H2>Connected services</H2>
        <KV k="Calling"><View style={s.badges}><Badge value={company.calls_connected ? "connected" : "not_connected"} /><Badge value={company.telephony_provider} tone="violet" /></View></KV>
        <KV k="WhatsApp"><Badge value={company.whatsapp_connected ? "connected" : "not_connected"} /></KV>
        <KV k="Inbound calls">
          <Badge value={!company.inbound_allowed ? "not in plan" : company.inbound_active ? "on" : "off"} tone={company.inbound_active ? "green" : "grey"} />
        </KV>
        <T small muted>Keys, numbers and login details are managed on the web portal.</T>
      </Card>
    </Screen>
  );
}

const s = StyleSheet.create({
  name: { fontSize: 17, flexShrink: 1 },
  form: { gap: 12 },
  badges: { flexDirection: "row", gap: 6, flexWrap: "wrap" },
});
