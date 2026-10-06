import type { KnowledgeDocument } from "@/api/types";
import { Badge, ListRow, PagedList, Row, T, fmtDate } from "@/components/ui";
import { useColors } from "@/theme";

/** The documents the AI answers from, and whether each one is ready. Uploading is done on the web portal. */
export default function Knowledge() {
  const c = useColors();
  return (
    <PagedList<KnowledgeDocument>
      queryKey={["knowledge"]}
      url="/knowledge/documents"
      refetchInterval={15_000}
      empty="No documents yet. Upload them on the web portal."
      renderItem={(d) => (
        <ListRow>
          <Row spread>
            <T bold style={{ flexShrink: 1 }}>{d.title}</T>
            <Badge value={d.status} />
          </Row>
          <T small muted>
            {d.domain.name} · {d.chunk_count} parts · updated {fmtDate(d.updated_at)}
          </T>
          {d.source_filename && <T small muted>{d.source_filename}</T>}
          {d.error && <T small style={{ color: c.red }}>{d.error}</T>}
        </ListRow>
      )}
    />
  );
}
