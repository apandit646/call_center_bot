import { useState } from "react";
import { useRouter } from "expo-router";
import type { InboundCall } from "@/api/types";
import { Badge, Chips, Input, ListRow, PagedList, Row, T, fmtDate, fmtDuration } from "@/components/ui";
import { useDebounced } from "@/hooks";

const OUTCOME = [
  ["", "All"], ["answered", "Answered by AI"], ["ticket_raised", "Ticket raised"], ["ticket_updated", "Ticket updated"],
  ["abandoned", "Abandoned"],
] as const;

/** Calls customers made to the company's number. Calls made by a campaign are listed inside that campaign. */
export default function Calls() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [outcome, setOutcome] = useState<(typeof OUTCOME)[number][0]>("");

  return (
    <PagedList<InboundCall>
      queryKey={["inbound-calls"]}
      url="/inbound-calls"
      params={{ q: useDebounced(q), outcome }}
      refetchInterval={15_000}
      empty="No inbound calls match these filters."
      header={
        <>
          <Input placeholder="Search caller name or number…" value={q} onChangeText={setQ} autoCorrect={false} returnKeyType="search" />
          <Chips options={OUTCOME} value={outcome} onChange={setOutcome} />
        </>
      }
      renderItem={(c) => (
        <ListRow onPress={() => router.push(`/conversations/${c.conversation_id}`)}>
          <Row spread>
            <T bold>{c.customer.name ?? c.from_number ?? "Hidden number"}</T>
            <T small muted>{fmtDate(c.started_at)}</T>
          </Row>
          <T small muted>
            {c.from_number ?? "Hidden number"} · {c.status === "in_progress" ? "on the line now" : fmtDuration(c.duration_seconds)}
            {c.has_recording ? " · recording" : ""}
          </T>
          <Row>
            {c.status === "in_progress" ? <Badge value="in_progress" /> : c.outcome && <Badge value={c.outcome} />}
            {c.ticket_number != null && <Badge value={`ticket #${c.ticket_number}`} tone="violet" />}
          </Row>
        </ListRow>
      )}
    />
  );
}
