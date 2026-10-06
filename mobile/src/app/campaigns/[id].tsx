import { Alert, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Call, Campaign, CampaignAnalytics } from "@/api/types";
import { useColors } from "@/theme";
import { Badge, Button, Card, ErrorBox, H2, KV, ListRow, Loading, PagedList, Row, Screen, Stat, T, fmtDate, fmtDuration } from "@/components/ui";

/** A campaign's progress and its calls. Editing the script, questions, contacts and schedule is done on the web portal. */
export default function CampaignDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const c = useColors();

  const { data: campaign, isLoading, error } = useQuery({
    queryKey: ["campaign", id],
    queryFn: async () => (await api.get<Campaign>(`/campaigns/${id}`)).data,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 10_000 : false),
  });
  const live = campaign?.status === "running";
  const { data: stats } = useQuery({
    queryKey: ["campaign-analytics", id],
    queryFn: async () => (await api.get<CampaignAnalytics>(`/campaigns/${id}/analytics`)).data,
    refetchInterval: live ? 10_000 : false,
    enabled: !!campaign,
  });

  const lifecycle = useMutation({
    mutationFn: async (action: "pause" | "resume" | "cancel") => api.post(`/campaigns/${id}/${action}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaign", id] });
      qc.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });

  if (isLoading) return <Loading />;
  if (error || !campaign) return <Screen><ErrorBox error={error ?? new Error("Campaign not found")} /></Screen>;
  const st = campaign.status;
  const confirmCancel = () =>
    Alert.alert("Cancel this campaign?", "Pending calls will not be made.", [
      { text: "Keep it", style: "cancel" },
      { text: "Cancel campaign", style: "destructive", onPress: () => lifecycle.mutate("cancel") },
    ]);
  const contacts = stats?.contacts;

  return (
    <>
      <Stack.Screen options={{ title: campaign.name }} />
      <PagedList<Call>
        queryKey={["campaign-calls", id]}
        url={`/campaigns/${id}/calls`}
        refetchInterval={live ? 5_000 : undefined}
        empty="No calls have been made yet."
        top={
          <>
            <ErrorBox error={lifecycle.error} />
            <Card>
              <Row>
                <Badge value={st} />
                <Badge value={campaign.conversational ? "AI conversation" : "question script"} tone="violet" />
              </Row>
              {campaign.description && <T muted>{campaign.description}</T>}
              {campaign.offering_name && <KV k={campaign.offering_type === "service" ? "Service" : "Product"}>{campaign.offering_name}</KV>}
              <KV k="Starts">{fmtDate(campaign.start_at)}</KV>
              <KV k="Call window">{`${campaign.call_window_start.slice(0, 5)} – ${campaign.call_window_end.slice(0, 5)} (${campaign.timezone})`}</KV>
              <KV k="Language">{[campaign.language, ...campaign.alt_languages].join(", ")}</KV>
              <Row>
                {(st === "running" || st === "scheduled") && (
                  <Button small variant="ghost" title="Pause" busy={lifecycle.isPending} onPress={() => lifecycle.mutate("pause")} />
                )}
                {st === "paused" && <Button small title="Resume" busy={lifecycle.isPending} onPress={() => lifecycle.mutate("resume")} />}
                {!["completed", "cancelled", "draft"].includes(st) && (
                  <Button small variant="danger" title="Cancel campaign" disabled={lifecycle.isPending} onPress={confirmCancel} />
                )}
              </Row>
              {st === "draft" && <T small muted>This campaign is a draft. Add contacts and schedule it on the web portal.</T>}
            </Card>

            {stats && contacts && (
              <View style={s.grid}>
                <Stat label="Contacts" value={contacts.total} />
                <Stat label="Completed" value={contacts.completed ?? 0} tone="green" hint={`${Math.round(stats.completion_rate * 100)}% completion`} />
                <Stat label="Still to call" value={(contacts.scheduled ?? 0) + (contacts.in_progress ?? 0) + (contacts.pending ?? 0)} tone="amber" />
                <Stat label="Failed" value={contacts.failed ?? 0} tone={contacts.failed ? "red" : "grey"} />
                <Stat label="Calls made" value={stats.calls.total} />
                <Stat label="Average call" value={fmtDuration(stats.avg_call_duration_seconds == null ? null : Math.round(stats.avg_call_duration_seconds))} tone="violet" />
              </View>
            )}
            <H2>Calls</H2>
          </>
        }
        renderItem={(call) => (
          <ListRow onPress={() => router.push(`/calls/${call.id}`)}>
            <Row spread>
              <T bold>{call.customer.name ?? call.customer.phone}</T>
              <T small muted>{fmtDate(call.started_at ?? call.created_at)}</T>
            </Row>
            <T small muted>
              {call.customer.phone} · attempt {call.attempt_number} · {fmtDuration(call.duration_seconds)}{call.has_recording ? " · recording" : ""}
            </T>
            <Row>
              <Badge value={call.status} />
              {call.all_questions_answered && <Badge value="all answered" tone="green" />}
            </Row>
            {call.error && <T small lines={2} style={{ color: c.red }}>{call.error}</T>}
          </ListRow>
        )}
      />
    </>
  );
}

const s = StyleSheet.create({
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
});
