import { useEffect, useRef, useState } from "react";
import { Alert, KeyboardAvoidingView, ScrollView, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useHeaderHeight } from "expo-router/react-navigation";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { customerContact, customerLabel } from "@/api/types";
import { api } from "@/api/client";
import type { ConversationDetail as TConversation, SupportCall } from "@/api/types";
import { RecordingPlayer } from "@/components/RecordingPlayer";
import { Badge, Bubble, Button, Card, ErrorBox, H2, Input, KV, Loading, Row, Screen, T, fmtDate, fmtDuration } from "@/components/ui";
import { useColors } from "@/theme";

const OUTCOME_TEXT: Record<string, string> = {
  answered: "The AI answered the caller from the knowledge base.",
  ticket_raised: "A ticket was raised for the team to call back.",
  ticket_updated: "The caller added details to a ticket they already had.",
  abandoned: "The caller hung up before saying what they needed.",
};
const LANGUAGE: Record<string, string> = { "en-IN": "English", "hi-IN": "Hindi" };

function CallSummary({ call, conversationId }: { call: SupportCall; conversationId: string }) {
  const live = call.status === "in_progress";
  return (
    <Card>
      <Row spread>
        <H2>Phone call</H2>
        {live ? <Badge value="in_progress" /> : call.outcome && <Badge value={call.outcome} />}
      </Row>
      <T small muted>{live ? "The caller is on the line with the AI agent now." : call.outcome ? OUTCOME_TEXT[call.outcome] : ""}</T>
      <KV k="Caller">{call.from_number ?? "Hidden number"}</KV>
      <KV k="Started">{fmtDate(call.started_at)}</KV>
      <KV k="Duration">{fmtDuration(call.duration_seconds)}</KV>
      <KV k="Language">{call.language ? LANGUAGE[call.language] ?? call.language : "—"}</KV>
      {call.has_recording && <RecordingPlayer path={`/conversations/${conversationId}/recording`} />}
    </Card>
  );
}

/** A WhatsApp chat (with a reply box) or the transcript of an inbound phone call. */
export default function ConversationDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const c = useColors();
  const headerHeight = useHeaderHeight();
  const insets = useSafeAreaInsets();
  const scroll = useRef<ScrollView>(null);
  const [text, setText] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["conversation", id],
    queryFn: async () => (await api.get<TConversation>(`/conversations/${id}`)).data,
    refetchInterval: 5_000,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["conversation", id] });
    qc.invalidateQueries({ queryKey: ["conversations"] });
  };

  const send = useMutation({
    mutationFn: async () => api.post(`/conversations/${id}/messages`, { text: text.trim() }),
    onSuccess: () => {
      setText("");
      refresh();
    },
  });
  const close = useMutation({ mutationFn: async () => api.post(`/conversations/${id}/close`), onSuccess: refresh });

  // A chat opens at its newest message and follows new ones. A call opens at the top: its summary and recording.
  const count = data?.messages.length;
  const chat = data ? data.channel !== "phone" : false;
  useEffect(() => {
    if (!chat) return;
    const timer = setTimeout(() => scroll.current?.scrollToEnd({ animated: true }), 100);
    return () => clearTimeout(timer);
  }, [count, chat]);

  if (isLoading) return <Loading />;
  if (error || !data) return <Screen><ErrorBox error={error ?? new Error("Conversation not found")} /></Screen>;
  const phone = data.channel === "phone";
  const email = data.channel === "email";
  const canReply = !phone && data.status !== "closed";
  const confirmClose = () =>
    Alert.alert("Close this conversation?", "The customer's next message will start a new one.", [
      { text: "Cancel", style: "cancel" },
      { text: "Close it", style: "destructive", onPress: () => close.mutate() },
    ]);

  return (
    <KeyboardAvoidingView style={[s.fill, { backgroundColor: c.bg }]} behavior="padding" keyboardVerticalOffset={headerHeight}>
      <Stack.Screen options={{ title: customerLabel(data.customer) }} />
      <ScrollView ref={scroll} contentContainerStyle={s.page} keyboardShouldPersistTaps="handled">
        <Card>
          <Row>
            <Badge value={data.channel} />
            <Badge value={data.status} />
          </Row>
          <T small muted selectable>
            {customerContact(data.customer)} · Domain: {data.domain?.name ?? "undetected"} · Started {fmtDate(data.created_at)}
          </T>
          {canReply && <Row><Button small variant="ghost" title="Close conversation" busy={close.isPending} onPress={confirmClose} /></Row>}
          <ErrorBox error={close.error} />
        </Card>

        {data.call && <CallSummary call={data.call} conversationId={data.id} />}

        {data.ticket_ids.length > 0 && (
          <Card>
            <T bold>{phone && data.call?.outcome === "ticket_updated" ? "Added to a ticket" : "Escalated to a ticket"}</T>
            <Row>
              {data.ticket_ids.map((t) => <Button key={t} small variant="ghost" title="View ticket" onPress={() => router.push(`/tickets/${t}`)} />)}
            </Row>
          </Card>
        )}

        {phone && <H2>Transcript</H2>}
        {data.messages.map((m) => (
          <Bubble
            key={m.id}
            mine={m.direction === "outbound"}
            agent={m.role === "agent"}
            text={m.content}
            meta={[
              phone ? (m.role === "customer" ? "caller" : "AI agent") : m.role,
              fmtDate(m.created_at),
              m.delivery_status,
            ].filter(Boolean).join(" · ")}
          />
        ))}
        {phone && (
          <T small muted>This was a phone call, so there is nothing to reply to here. Follow up from the ticket, or call the customer back.</T>
        )}
      </ScrollView>

      {canReply && (
        <View style={[s.reply, { backgroundColor: c.panel, borderTopColor: c.border, paddingBottom: Math.max(insets.bottom, 10) }]}>
          <ErrorBox error={send.error} />
          <View style={s.replyLine}>
            <View style={s.fill}>
              <Input placeholder={email ? "Reply by email as a human agent…" : "Reply on WhatsApp as a human agent…"} value={text} onChangeText={setText} multiline style={s.replyInput} />
            </View>
            <Button title="Send" disabled={!text.trim()} busy={send.isPending} onPress={() => send.mutate()} />
          </View>
        </View>
      )}
    </KeyboardAvoidingView>
  );
}

const s = StyleSheet.create({
  fill: { flex: 1 },
  page: { padding: 14, gap: 10, paddingBottom: 24 },
  reply: { borderTopWidth: 1, paddingHorizontal: 12, paddingTop: 10, gap: 8 },
  replyLine: { flexDirection: "row", alignItems: "flex-end", gap: 8 },
  replyInput: { minHeight: 46, maxHeight: 120 },
});
