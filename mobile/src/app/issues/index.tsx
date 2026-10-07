import { useState } from "react";
import { Stack, useRouter } from "expo-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { IssueDetail, IssueReport } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Button, Chips, ErrorBox, Input, ListRow, PagedList, Row, Sheet, T, fmtDate } from "@/components/ui";
import { CATEGORIES, CATEGORY, PRIORITY_TONE, useIssueSummary } from "@/issues";

const PRIORITIES = [["low", "Low"], ["normal", "Normal"], ["high", "High: stopping our work"]] as const;

/** Report a problem with the platform, and (for admins) deal with the reports addressed to you. */
export default function Issues() {
  const router = useRouter();
  const qc = useQueryClient();
  const { user } = useAuth();
  const { data: summary } = useIssueSummary();
  const platform = user?.role === "super_admin";
  const hasInbox = user?.role === "admin" || platform;
  const [box, setBox] = useState<"mine" | "inbox">(platform ? "inbox" : "mine");
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ subject: "", description: "", category: "other", priority: "normal" });

  const create = useMutation({
    mutationFn: async () => (await api.post<IssueDetail>("/issues", form)).data,
    onSuccess: (report) => {
      qc.invalidateQueries({ queryKey: ["issues"] });
      qc.invalidateQueries({ queryKey: ["issues-summary"] });
      setCreating(false);
      setForm({ subject: "", description: "", category: "other", priority: "normal" });
      setBox("mine");
      router.push(`/issues/${report.id}`);
    },
  });
  const boxes = [["mine", "My reports"], ["inbox", `From my team${summary?.inbox_open ? ` (${summary.inbox_open})` : ""}`]] as const;

  return (
    <>
      <Stack.Screen options={{ title: platform ? "Issue reports" : "Report an issue" }} />
      <PagedList<IssueReport>
        queryKey={["issues", box]}
        url="/issues"
        params={{ box }}
        refetchInterval={30_000}
        empty={box === "inbox" ? "No reports for you to deal with." : "You have not reported anything yet."}
        header={
          <>
            {!platform && <T small muted>Something not working? Your reports go to {summary?.reports_go_to ?? "your company admin"}.</T>}
            {!platform && <Button title="Report an issue" onPress={() => { create.reset(); setCreating(true); }} />}
            {hasInbox && !platform && <Chips options={boxes} value={box} onChange={setBox} />}
          </>
        }
        renderItem={(i) => (
          <ListRow onPress={() => router.push(`/issues/${i.id}`)}>
            <Row spread>
              <T bold style={{ flexShrink: 1 }}>#{i.number} · {i.subject}</T>
              <T small muted>{fmtDate(i.created_at)}</T>
            </Row>
            <T small muted>
              {CATEGORY[i.category] ?? i.category}
              {box === "inbox" ? ` · ${i.reporter?.full_name ?? "Former user"}${platform && i.company_name ? `, ${i.company_name}` : ""}` : ""}
              {` · ${i.comment_count} ${i.comment_count === 1 ? "reply" : "replies"}`}
            </T>
            <Row>
              <Badge value={i.status} />
              <Badge value={i.priority} tone={PRIORITY_TONE[i.priority]} />
              {i.escalated_to && <Badge value={`sent on as #${i.escalated_to.number}`} tone="violet" />}
            </Row>
          </ListRow>
        )}
      />
      {creating && (
        <Sheet title="Report an issue" onClose={() => setCreating(false)}>
          <T small muted>This goes to {summary?.reports_go_to ?? "your company admin"}. Say what you were doing and what happened instead.</T>
          <ErrorBox error={create.error} />
          <Input label="Subject" value={form.subject} onChangeText={(subject) => setForm((f) => ({ ...f, subject }))} maxLength={200} placeholder="e.g. Inbound calls drop after 10 seconds" />
          <T small muted bold>What is it about?</T>
          <Chips options={CATEGORIES} value={form.category as (typeof CATEGORIES)[number][0]} onChange={(category) => setForm((f) => ({ ...f, category }))} />
          <T small muted bold>How urgent?</T>
          <Chips options={PRIORITIES} value={form.priority as (typeof PRIORITIES)[number][0]} onChange={(priority) => setForm((f) => ({ ...f, priority }))} />
          <Input label="What happened?" value={form.description} onChangeText={(description) => setForm((f) => ({ ...f, description }))} multiline maxLength={5000}
            placeholder="When did it start? Which customer, campaign or call? What did you see?" />
          <Button title="Send report" busy={create.isPending} disabled={form.subject.trim().length < 3 || form.description.trim().length < 5} onPress={() => create.mutate()} />
        </Sheet>
      )}
    </>
  );
}
