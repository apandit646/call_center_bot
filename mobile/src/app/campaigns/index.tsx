import { useState } from "react";
import { useRouter } from "expo-router";
import type { Campaign } from "@/api/types";
import { Badge, Chips, ListRow, PagedList, Row, T, fmtDate } from "@/components/ui";

const STATUS = [
  ["", "All"], ["running", "Running"], ["scheduled", "Scheduled"], ["paused", "Paused"], ["draft", "Draft"],
  ["completed", "Completed"], ["cancelled", "Cancelled"],
] as const;

export default function Campaigns() {
  const router = useRouter();
  const [status, setStatus] = useState<(typeof STATUS)[number][0]>("");
  return (
    <PagedList<Campaign>
      queryKey={["campaigns"]}
      url="/campaigns"
      params={{ status }}
      refetchInterval={30_000}
      empty="No campaigns here. Campaigns are created on the web portal."
      header={<Chips options={STATUS} value={status} onChange={setStatus} />}
      renderItem={(c) => (
        <ListRow onPress={() => router.push(`/campaigns/${c.id}`)}>
          <Row spread>
            <T bold style={{ flexShrink: 1 }}>{c.name}</T>
            <Badge value={c.status} />
          </Row>
          {c.offering_name && <T small muted>{c.offering_type}: {c.offering_name}</T>}
          <T small muted>
            {c.completed_count ?? 0} of {c.contact_count ?? 0} contacts completed · {c.start_at ? `starts ${fmtDate(c.start_at)}` : `created ${fmtDate(c.created_at)}`}
          </T>
        </ListRow>
      )}
    />
  );
}
