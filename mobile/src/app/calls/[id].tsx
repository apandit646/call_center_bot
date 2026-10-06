import { View } from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { CallDetail as TCall, Campaign } from "@/api/types";
import { RecordingPlayer } from "@/components/RecordingPlayer";
import { Badge, Bubble, Card, Empty, ErrorBox, H2, KV, Loading, Notice, Row, Screen, T, fmtDate, fmtDuration, fmtValue } from "@/components/ui";

/** One call made by a campaign: what was said, the answers taken from it, and the recording. */
export default function CallDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: call, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: ["call", id],
    queryFn: async () => (await api.get<TCall>(`/calls/${id}`)).data,
  });
  const { data: campaign } = useQuery({
    queryKey: ["campaign", call?.campaign_id],
    queryFn: async () => (await api.get<Campaign>(`/campaigns/${call!.campaign_id}`)).data,
    enabled: !!call,
  });

  if (isLoading) return <Loading />;
  if (error || !call) return <Screen><ErrorBox error={error ?? new Error("Call not found")} /></Screen>;

  // Answers in the order the questions were asked, with the question's own wording.
  const questions = campaign?.questions ?? [];
  const answerFor = new Map(call.answers.map((a) => [a.question_id, a]));
  const ordered = questions.length
    ? questions.filter((q) => q.id && answerFor.has(q.id)).map((q) => ({ q, a: answerFor.get(q.id!)! }))
    : call.answers.map((a) => ({ q: undefined, a }));

  return (
    <Screen refreshing={isRefetching} onRefresh={refetch}>
      <Stack.Screen options={{ title: call.customer.name ?? call.customer.phone }} />
      {call.error && <Notice tone="red">{call.error}</Notice>}
      <Card>
        <Row>
          <Badge value={call.status} />
          {call.all_questions_answered && <Badge value="all answered" tone="green" />}
        </Row>
        <KV k="Customer">{`${call.customer.name ?? "—"} · ${call.customer.phone}`}</KV>
        {campaign && <KV k="Campaign">{campaign.name}</KV>}
        <KV k="Attempt">{call.attempt_number}</KV>
        <KV k="Started">{fmtDate(call.started_at ?? call.created_at)}</KV>
        <KV k="Duration">{fmtDuration(call.duration_seconds)}</KV>
        <KV k="Provider">{call.provider}</KV>
        {call.has_recording && <RecordingPlayer path={`/calls/${call.id}/recording`} />}
      </Card>

      {ordered.length > 0 && (
        <Card>
          <H2>Answers</H2>
          {ordered.map(({ q, a }) => (
            <View key={a.question_id}>
              <T small muted>{q?.text ?? "Question"}</T>
              <Row>
                <T bold selectable>{fmtValue(a.normalized_answer.value)}</T>
                <Badge value={a.status} />
              </Row>
              {a.raw_answer && <T small muted selectable>“{a.raw_answer}”</T>}
            </View>
          ))}
        </Card>
      )}

      <H2>Transcript</H2>
      {call.transcript.length === 0 && <Empty>Nothing was said on this call.</Empty>}
      {call.transcript.map((t) => (
        <Bubble key={t.turn_index} mine={t.speaker === "ai"} text={t.text} meta={`${t.speaker === "ai" ? "AI agent" : "customer"} · ${fmtDate(t.created_at)}`} />
      ))}
    </Screen>
  );
}
