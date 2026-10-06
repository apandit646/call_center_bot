import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { ConversationDetail, TicketDetail as TTicket, TicketEvent } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Bubble, Button, Card, Chips, ErrorBox, H2, Input, KV, Loading, Row, Screen, T, fmtDate, label } from "@/components/ui";
import { useAgents } from "@/hooks";
import { useColors } from "@/theme";

const STATUSES = ["open", "in_progress", "pending_customer", "resolved", "closed"] as const;
const PRIORITIES = ["low", "medium", "high", "urgent"] as const;

function describe(e: TicketEvent): string {
  const d = e.data as Record<string, string | null>;
  switch (e.event_type) {
    case "created": return `Ticket created from ${d.source}`;
    case "assigned": return d.to_agent_id ? `Assigned${d.auto ? " automatically" : ""}` : "Unassigned";
    case "status_changed": return `Status: ${d.from} → ${d.to}`;
    case "priority_changed": return `Priority: ${d.from} → ${d.to}`;
    case "customer_message": return `${d.channel === "phone" ? "Customer said on a call" : "Customer wrote"}: “${d.text}”`;
    case "comment": return d.text ?? "";
    default: return e.event_type;
  }
}

export default function TicketDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const c = useColors();
  const { user, hasRole } = useAuth();
  const manager = hasRole("admin", "supervisor");
  const { data: agents } = useAgents(manager);
  const [comment, setComment] = useState("");

  const { data: ticket, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: ["ticket", id],
    queryFn: async () => (await api.get<TTicket>(`/tickets/${id}`)).data,
  });
  const { data: conversation } = useQuery({
    queryKey: ["conversation", ticket?.conversation_id],
    queryFn: async () => (await api.get<ConversationDetail>(`/conversations/${ticket!.conversation_id}`)).data,
    enabled: !!ticket?.conversation_id,
  });

  const mutate = useMutation({
    mutationFn: async (req: { url: string; body: unknown; method?: "post" | "patch" }) =>
      (await api[req.method ?? "post"]<TTicket>(req.url, req.body)).data,
    onSuccess: (data) => {
      qc.setQueryData(["ticket", id], data);
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  if (isLoading) return <Loading />;
  if (error || !ticket) return <Screen><ErrorBox error={error ?? new Error("Ticket not found")} /></Screen>;
  const phone = conversation?.channel === "phone";
  const recent = conversation?.messages.slice(-6) ?? [];

  return (
    <Screen refreshing={isRefetching} onRefresh={refetch}>
      <Stack.Screen options={{ title: `Ticket #${ticket.number}` }} />
      <ErrorBox error={mutate.error} />

      <Card>
        <T bold style={s.issue} selectable>{ticket.issue}</T>
        <KV k="Customer">{`${ticket.customer.name ?? "—"} · ${ticket.customer.phone}`}</KV>
        <KV k="Domain">{ticket.domain?.name ?? "—"}</KV>
        <KV k="Source"><Badge value={ticket.source} /></KV>
        <KV k="Created">{fmtDate(ticket.created_at)}</KV>
        {ticket.resolved_at && <KV k="Resolved">{fmtDate(ticket.resolved_at)}</KV>}
      </Card>

      <Card>
        <H2>Status</H2>
        <Chips
          options={STATUSES.map((v) => [v, label(v)] as const)}
          value={ticket.status}
          onChange={(v) => v !== ticket.status && mutate.mutate({ url: `/tickets/${id}`, method: "patch", body: { status: v } })}
        />
        <View style={s.gap} />
        <H2>Priority</H2>
        <Chips
          options={PRIORITIES.map((v) => [v, v] as const)}
          value={ticket.priority}
          onChange={(v) => v !== ticket.priority && mutate.mutate({ url: `/tickets/${id}`, method: "patch", body: { priority: v } })}
        />
        <View style={s.gap} />
        <H2>Assigned to</H2>
        {manager ? (
          <Chips
            options={[["", "Unassigned"] as const, ...(agents ?? []).map((a) => [a.id, a.full_name] as const)]}
            value={ticket.assigned_to?.id ?? ""}
            onChange={(v) => v !== (ticket.assigned_to?.id ?? "") && mutate.mutate({ url: `/tickets/${id}/assign`, body: { agent_id: v || null } })}
          />
        ) : (
          <Row>
            <T>{ticket.assigned_to?.full_name ?? "Unassigned"}</T>
            {!ticket.assigned_to && (
              <Button small title="Take it" busy={mutate.isPending} onPress={() => mutate.mutate({ url: `/tickets/${id}/assign`, body: { agent_id: user!.id } })} />
            )}
          </Row>
        )}
      </Card>

      <Card>
        <H2>AI summary</H2>
        <T selectable>{ticket.ai_summary ?? "—"}</T>
      </Card>

      {conversation && (
        <Card style={s.chat}>
          <H2>{phone ? "Call transcript" : "Conversation"}</H2>
          {conversation.messages.length > recent.length && <T small muted>Showing the last {recent.length} of {conversation.messages.length} messages.</T>}
          {recent.map((m) => (
            <Bubble
              key={m.id}
              mine={m.direction === "outbound"}
              agent={m.role === "agent"}
              text={m.content}
              meta={`${phone ? (m.role === "customer" ? "caller" : "AI agent") : m.role} · ${fmtDate(m.created_at)}`}
            />
          ))}
          <Button
            small variant="ghost"
            title={phone ? "Open the call: recording and details" : "Open the conversation to reply on WhatsApp"}
            onPress={() => router.push(`/conversations/${conversation.id}`)}
          />
        </Card>
      )}

      <Card>
        <H2>Activity</H2>
        {ticket.events.map((e) => (
          <View key={e.id} style={[s.event, { borderLeftColor: c.border }]}>
            <T selectable>{describe(e)}</T>
            <T small muted>{e.actor?.full_name ?? "System"} · {fmtDate(e.created_at)}</T>
          </View>
        ))}
        <Input placeholder="Add an internal note…" value={comment} onChangeText={setComment} multiline />
        <Button
          title="Add note"
          disabled={!comment.trim()}
          busy={mutate.isPending}
          onPress={() => mutate.mutate({ url: `/tickets/${id}/comments`, body: { text: comment.trim() } }, { onSuccess: () => setComment("") })}
        />
      </Card>
    </Screen>
  );
}

const s = StyleSheet.create({
  issue: { fontSize: 16, marginBottom: 4 },
  gap: { height: 8 },
  chat: { gap: 8 },
  event: { borderLeftWidth: 2, paddingLeft: 10, marginBottom: 8 },
});
