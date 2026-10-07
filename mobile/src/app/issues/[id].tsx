import { useState } from "react";
import { KeyboardAvoidingView, ScrollView, StyleSheet, View } from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";
import { useHeaderHeight } from "expo-router/react-navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { IssueDetail } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Bubble, Button, Card, ErrorBox, H2, Input, Loading, Notice, Row, Screen, T, fmtDate } from "@/components/ui";
import { useColors } from "@/theme";
import { CATEGORY, PRIORITY_TONE } from "@/issues";

const STATUSES = ["open", "in_progress", "resolved", "closed"] as const;

/** One report: what was said, the replies, and (for whoever handles it) its status and passing it on. */
export default function IssueView() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const qc = useQueryClient();
  const c = useColors();
  const headerHeight = useHeaderHeight();
  const { user } = useAuth();
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [escalating, setEscalating] = useState(false);
  const key = ["issue", id];

  const { data: report, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: async () => (await api.get<IssueDetail>(`/issues/${id}`)).data,
    refetchInterval: 15_000,
  });
  const done = (updated?: IssueDetail) => {
    if (updated && updated.id === id) qc.setQueryData(key, updated);
    else qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["issues"] });
    qc.invalidateQueries({ queryKey: ["issues-summary"] });
  };
  const reply = useMutation({
    mutationFn: async () => (await api.post<IssueDetail>(`/issues/${id}/comments`, { text: text.trim() })).data,
    onSuccess: (d) => { setText(""); done(d); },
  });
  const setStatus = useMutation({
    mutationFn: async (status: string) => (await api.patch<IssueDetail>(`/issues/${id}`, { status })).data,
    onSuccess: done,
  });
  const escalate = useMutation({
    mutationFn: async () => api.post(`/issues/${id}/escalate`, { note }),
    onSuccess: () => { setEscalating(false); setNote(""); done(); },
  });

  if (isLoading) return <Loading />;
  if (error || !report) return <Screen><ErrorBox error={error ?? new Error("Report not found")} /></Screen>;

  return (
    <KeyboardAvoidingView style={[s.fill, { backgroundColor: c.bg }]} behavior="padding" keyboardVerticalOffset={headerHeight}>
      <Stack.Screen options={{ title: `Report #${report.number}` }} />
      <ScrollView contentContainerStyle={s.page} keyboardShouldPersistTaps="handled">
        <Card>
          <T bold style={s.subject} selectable>{report.subject}</T>
          <Row>
            <Badge value={report.status} />
            <Badge value={report.priority} tone={PRIORITY_TONE[report.priority]} />
            <Badge value={CATEGORY[report.category] ?? report.category} tone="grey" />
          </Row>
          <T small muted>
            {report.reporter?.full_name ?? "Former user"}{report.reporter_role ? ` (${report.reporter_role.replace("_", " ")})` : ""}
            {user?.role === "super_admin" && report.company_name ? ` · ${report.company_name}` : ""} · {fmtDate(report.created_at)}
          </T>
          <T small muted>Sent to {report.level === "platform" ? "the platform admin" : "the company admin"}</T>
          <T selectable style={s.body}>{report.description}</T>
        </Card>
        {report.escalated_to && <Notice tone="violet">Sent to the platform administrator as report #{report.escalated_to.number}, which is {report.escalated_to.status.replace("_", " ")}.</Notice>}

        {report.comments.map((m) => m.kind === "status" ? (
          <T key={m.id} small muted style={s.event}>{m.text} · {m.author?.full_name ?? "System"} · {fmtDate(m.created_at)}</T>
        ) : (
          <Bubble key={m.id} mine={m.author?.id === user?.id} text={m.text} meta={`${m.author?.full_name ?? "Former user"} · ${fmtDate(m.created_at)}`} />
        ))}

        <ErrorBox error={reply.error || setStatus.error || escalate.error} />
        {report.can_manage && (
          <Card style={s.manage}>
            <H2>Handle this report</H2>
            <Row>
              {STATUSES.filter((st) => st !== report.status).map((st) => (
                <Button key={st} small variant={st === "resolved" ? "primary" : "ghost"} title={`Mark ${st.replace("_", " ")}`} disabled={setStatus.isPending} onPress={() => setStatus.mutate(st)} />
              ))}
            </Row>
            {report.can_escalate && !escalating && <Button small variant="ghost" title="Send to the platform admin…" onPress={() => setEscalating(true)} />}
            {escalating && (
              <>
                <T small muted>The platform admin gets this report in the words of the person who wrote it. Add anything you already checked.</T>
                <Input value={note} onChangeText={setNote} multiline placeholder="Optional note for the platform admin" />
                <Row>
                  <Button small title="Send to the platform admin" busy={escalate.isPending} onPress={() => escalate.mutate()} />
                  <Button small variant="ghost" title="Cancel" onPress={() => setEscalating(false)} />
                </Row>
              </>
            )}
          </Card>
        )}
        {report.status === "closed" && <T small muted>This report is closed. If the problem comes back, report it again.</T>}
      </ScrollView>

      {report.status !== "closed" && (
        <View style={[s.reply, { backgroundColor: c.panel, borderTopColor: c.border }]}>
          <View style={s.fill}>
            <Input value={text} onChangeText={setText} multiline style={s.replyInput}
              placeholder={report.can_manage ? "Reply to the person who reported this…" : "Add detail or answer a question…"} />
          </View>
          <Button title="Send" disabled={!text.trim()} busy={reply.isPending} onPress={() => reply.mutate()} />
        </View>
      )}
    </KeyboardAvoidingView>
  );
}

const s = StyleSheet.create({
  fill: { flex: 1 },
  page: { padding: 14, gap: 10, paddingBottom: 24 },
  subject: { fontSize: 17 },
  body: { marginTop: 6 },
  event: { textAlign: "center" },
  manage: { gap: 10 },
  reply: { flexDirection: "row", alignItems: "flex-end", gap: 8, borderTopWidth: 1, paddingHorizontal: 12, paddingTop: 10, paddingBottom: 14 },
  replyInput: { minHeight: 46, maxHeight: 120 },
});
