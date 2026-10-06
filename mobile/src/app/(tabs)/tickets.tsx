import { useState } from "react";
import { useRouter } from "expo-router";
import type { Ticket } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Chips, Input, ListRow, PagedList, Row, T, fmtDate } from "@/components/ui";
import { useDebounced } from "@/hooks";

const STATUS = [
  ["", "All"], ["open", "Open"], ["in_progress", "In progress"], ["pending_customer", "Pending customer"],
  ["resolved", "Resolved"], ["closed", "Closed"],
] as const;

export default function Tickets() {
  const router = useRouter();
  const { hasRole } = useAuth();
  const manager = hasRole("admin", "supervisor");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<(typeof STATUS)[number][0]>("");
  // An agent sees the tickets assigned to them, plus the unassigned ones they can take.
  const [scope, setScope] = useState<"" | "mine" | "unassigned">(manager ? "" : "mine");
  const scopes = manager
    ? ([["", "Everyone's"], ["mine", "Mine"], ["unassigned", "Unassigned"]] as const)
    : ([["mine", "Mine"], ["unassigned", "Unassigned"]] as const);

  return (
    <PagedList<Ticket>
      queryKey={["tickets"]}
      url="/tickets"
      params={{ q: useDebounced(q), status, mine: scope === "mine", unassigned: scope === "unassigned" }}
      refetchInterval={30_000}
      empty="No tickets match these filters."
      header={
        <>
          <Input placeholder="Search #number, issue, customer…" value={q} onChangeText={setQ} autoCorrect={false} returnKeyType="search" />
          <Chips options={scopes} value={scope} onChange={setScope} />
          <Chips options={STATUS} value={status} onChange={setStatus} />
        </>
      }
      renderItem={(t) => (
        <ListRow onPress={() => router.push(`/tickets/${t.id}`)}>
          <Row spread>
            <T bold>#{t.number} · {t.customer.name ?? t.customer.phone}</T>
            <T small muted>{fmtDate(t.created_at)}</T>
          </Row>
          <T lines={2}>{t.issue}</T>
          <Row>
            <Badge value={t.status} />
            <Badge value={t.priority} />
            <Badge value={t.source} />
          </Row>
          <T small muted>{t.assigned_to ? `Assigned to ${t.assigned_to.full_name}` : "Unassigned"}{t.domain ? ` · ${t.domain.name}` : ""}</T>
        </ListRow>
      )}
    />
  );
}
