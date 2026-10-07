import { useState } from "react";
import { Alert } from "react-native";
import { useRouter } from "expo-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { Callback, CampaignCallback } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Button, Chips, ErrorBox, ListRow, PagedList, Row, Sheet, T, fmtDate, type Tone } from "@/components/ui";

const KIND = [["inbound", "From inbound calls"], ["campaign", "From campaign calls"]] as const;
const SHOW = [["upcoming", "To make"], ["finished", "Made or cancelled"]] as const;
const CAMPAIGN_STATUS: Record<CampaignCallback["status"], { label: string; tone: Tone }> = {
  waiting: { label: "waiting", tone: "blue" }, on_hold: { label: "on hold", tone: "amber" }, overdue: { label: "overdue", tone: "red" },
  calling: { label: "calling", tone: "amber" }, done: { label: "done", tone: "green" }, not_reached: { label: "not reached", tone: "red" },
  cancelled: { label: "cancelled", tone: "grey" },
};

/** Times offered when moving a call-back; a phone has no quick way to type a date and time. */
function choices(): [string, Date][] {
  const at = (days: number, hour: number) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    d.setHours(hour, 0, 0, 0);
    return d;
  };
  const inMinutes = (m: number) => new Date(Date.now() + m * 60_000);
  return [
    ["In 30 minutes", inMinutes(30)], ["In 1 hour", inMinutes(60)], ["In 2 hours", inMinutes(120)],
    ["Today 5 PM", at(0, 17)], ["Tomorrow 10 AM", at(1, 10)], ["Tomorrow 3 PM", at(1, 15)], ["Tomorrow 6 PM", at(1, 18)],
  ].filter(([, d]) => (d as Date).getTime() > Date.now() + 5 * 60_000) as [string, Date][];
}

/** Every promised call-back in one place: asked for on an inbound call, or on a campaign call. */
export default function Callbacks() {
  const router = useRouter();
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const manager = hasRole("admin", "supervisor");
  const [kind, setKind] = useState<(typeof KIND)[number][0]>("inbound");
  const [show, setShow] = useState<(typeof SHOW)[number][0]>("upcoming");
  // What is being moved: its address on the API, the field the new time goes in, and who it is for.
  const [moving, setMoving] = useState<{ url: string; field: string; name: string } | null>(null);

  const done = () => {
    qc.invalidateQueries({ queryKey: ["callbacks"] });
    qc.invalidateQueries({ queryKey: ["campaign-callbacks"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const cancel = useMutation({ mutationFn: async (url: string) => api.post(`${url}/cancel`), onSuccess: done });
  const move = useMutation({
    mutationFn: async (when: Date) => api.patch(moving!.url, { [moving!.field]: when.toISOString() }),
    onSuccess: () => { setMoving(null); done(); },
  });
  const confirmCancel = (url: string, name: string) =>
    Alert.alert(`Cancel the call-back to ${name}?`, "They will not be called.", [
      { text: "Keep it", style: "cancel" },
      { text: "Cancel call-back", style: "destructive", onPress: () => cancel.mutate(url) },
    ]);
  const startMove = (url: string, field: string, name: string) => { move.reset(); setMoving({ url, field, name }); };

  const header = (
    <>
      <Chips options={KIND} value={kind} onChange={setKind} />
      {kind === "campaign" && <Chips options={SHOW} value={show} onChange={setShow} />}
      <ErrorBox error={cancel.error} />
    </>
  );

  return (
    <>
      {kind === "inbound" ? (
        <PagedList<Callback>
          queryKey={["callbacks"]}
          url="/callbacks"
          refetchInterval={15_000}
          header={header}
          empty={'No callbacks yet. When a caller says "call me back at 5", it appears here.'}
          renderItem={(c) => {
            const name = c.customer.name ?? c.phone;
            return (
              <ListRow onPress={c.conversation_id ?? c.source_conversation_id ? () => router.push(`/conversations/${c.conversation_id ?? c.source_conversation_id}`) : undefined}>
                <Row spread>
                  <T bold style={{ flexShrink: 1 }}>{name}</T>
                  <Badge value={c.status === "failed" ? "not reached" : c.status} tone={c.status === "failed" ? "red" : undefined} />
                </Row>
                <T>Call at {fmtDate(c.due_at)}{c.attempts > 0 ? ` · ${c.attempts} ${c.attempts === 1 ? "try" : "tries"}` : ""}</T>
                {c.reason && <T small muted lines={2}>{c.reason}</T>}
                {c.error && c.status !== "completed" && <T small muted>{c.error}</T>}
                <Row>
                  {["scheduled", "failed", "cancelled"].includes(c.status) && (
                    <Button small variant="ghost" title={c.status === "scheduled" ? "Change time" : "Schedule again"} onPress={() => startMove(`/callbacks/${c.id}`, "due_at", name)} />
                  )}
                  {c.status === "scheduled" && <Button small variant="danger" title="Cancel" onPress={() => confirmCancel(`/callbacks/${c.id}`, name)} />}
                </Row>
              </ListRow>
            );
          }}
        />
      ) : (
        <PagedList<CampaignCallback>
          queryKey={["campaign-callbacks"]}
          url="/callbacks/campaign"
          params={{ show }}
          refetchInterval={15_000}
          header={header}
          empty={show === "upcoming" ? 'No campaign call-backs waiting. When a customer on a campaign call says "call me later", it appears here.' : "Nothing here yet."}
          renderItem={(c) => {
            const name = c.customer.name ?? c.customer.phone ?? "this customer";
            const st = CAMPAIGN_STATUS[c.status];
            return (
              <ListRow onPress={c.call_id ? () => router.push(`/calls/${c.call_id}`) : undefined}>
                <Row spread>
                  <T bold style={{ flexShrink: 1 }}>{name}</T>
                  <Badge value={st.label} tone={st.tone} />
                </Row>
                <T>Call at {fmtDate(c.scheduled_at)}</T>
                <T small muted>
                  {c.campaign_name}{c.run != null && c.run > 1 ? ` · run ${c.run}` : ""} · {c.chosen_by_customer ? "time chosen by the customer" : "automatic retry"}
                </T>
                {c.reason && <T small muted>{c.reason}</T>}
                {c.can_edit && manager && (
                  <Row>
                    <Button small variant="ghost" title="Change time" onPress={() => startMove(`/callbacks/campaign/${c.id}`, "scheduled_at", name)} />
                    <Button small variant="danger" title="Cancel" onPress={() => confirmCancel(`/callbacks/campaign/${c.id}`, name)} />
                  </Row>
                )}
              </ListRow>
            );
          }}
        />
      )}
      {moving && (
        <Sheet title={`Call ${moving.name} at`} onClose={() => setMoving(null)}>
          <ErrorBox error={move.error} />
          <T small muted>Pick a time. A campaign call-back must be inside the calling hours of its campaign.</T>
          {choices().map(([label, when]) => (
            <Button key={label} variant="ghost" title={`${label} (${fmtDate(when.toISOString())})`} disabled={move.isPending} onPress={() => move.mutate(when)} />
          ))}
        </Sheet>
      )}
    </>
  );
}
