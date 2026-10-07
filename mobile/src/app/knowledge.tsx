import { useState } from "react";
import { useRouter } from "expo-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { KnowledgeDocument, KnowledgeGap } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Button, Chips, ErrorBox, ListRow, PagedList, Row, T, fmtDate } from "@/components/ui";
import { useColors } from "@/theme";

const VIEW = [["documents", "Documents"], ["open", "Unanswered"], ["resolved", "Handled"]] as const;
const WHY: Record<string, string> = {
  no_match: "Nothing in the knowledge base matched", cannot_answer: "The documents found did not answer it",
};

/** The documents the AI answers from, and the questions it could not answer. Uploading is done on the web portal. */
export default function Knowledge() {
  const c = useColors();
  const router = useRouter();
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const manager = hasRole("admin", "supervisor");
  const [view, setView] = useState<(typeof VIEW)[number][0]>("documents");
  const act = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "resolve" | "reopen" }) => api.post(`/knowledge/gaps/${id}/${action}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge-gaps"] }),
  });
  const header = (
    <>
      <Chips options={VIEW} value={view} onChange={setView} />
      {view !== "documents" && <T small muted>Customers asked these and the assistant had no answer. Add a document that covers the question (on the web portal), then mark it handled.</T>}
      <ErrorBox error={act.error} />
    </>
  );

  if (view === "documents") {
    return (
      <PagedList<KnowledgeDocument>
        queryKey={["knowledge"]}
        url="/knowledge/documents"
        refetchInterval={15_000}
        header={header}
        empty="No documents yet. Upload them on the web portal."
        renderItem={(d) => (
          <ListRow>
            <Row spread>
              <T bold style={{ flexShrink: 1 }}>{d.title}</T>
              <Badge value={d.status} />
            </Row>
            <T small muted>{d.domain.name} · {d.chunk_count} parts · updated {fmtDate(d.updated_at)}</T>
            {d.status === "failed" && d.error && <T small style={{ color: c.red }}>{d.error}</T>}
          </ListRow>
        )}
      />
    );
  }
  return (
    <PagedList<KnowledgeGap>
      queryKey={["knowledge-gaps", view]}
      url="/knowledge/gaps"
      params={{ status: view }}
      header={header}
      empty={view === "open" ? "Nothing waiting: the assistant has answered everything it was asked." : "Nothing marked handled yet."}
      renderItem={(g) => (
        <ListRow onPress={g.conversation_id ? () => router.push(`/conversations/${g.conversation_id}`) : undefined}>
          <T bold selectable>{g.question}</T>
          <Row>
            <Badge value={g.channel} />
            {g.domain && <Badge value={g.domain.name} tone="grey" />}
          </Row>
          <T small muted>{WHY[g.reason] ?? g.reason} · {fmtDate(g.created_at)}</T>
          {manager && (
            <Row>
              <Button small variant="ghost" title={g.status === "open" ? "Mark handled" : "Reopen"} disabled={act.isPending}
                onPress={() => act.mutate({ id: g.id, action: g.status === "open" ? "resolve" : "reopen" })} />
            </Row>
          )}
        </ListRow>
      )}
    />
  );
}
