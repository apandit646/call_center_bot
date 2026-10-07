import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { KnowledgeGap, Page } from "../../api/types";
import { Badge, Empty, ErrorBox, Loading, Pagination, fmtDate } from "../../components/ui";

const WHY: Record<KnowledgeGap["reason"], string> = {
  no_match: "Nothing in the knowledge base matched",
  cannot_answer: "The documents found did not answer it",
};

/** Questions the assistant could not answer, on any channel: the list of what to add to the knowledge base. */
export default function Unanswered({ manager }: { manager: boolean }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const [page, setPage] = useState(1);
  const { data, isLoading, error } = useQuery({
    queryKey: ["knowledge-gaps", status, page],
    queryFn: async () => (await api.get<Page<KnowledgeGap>>("/knowledge/gaps", { params: { status, page } })).data,
    placeholderData: keepPreviousData,
  });
  const act = useMutation({
    mutationFn: async ({ id, action }: { id: string; action: "resolve" | "reopen" }) => api.post(`/knowledge/gaps/${id}/${action}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge-gaps"] }),
  });

  return (
    <>
      <div className="filters spread row">
        <div className="tabs" style={{ marginBottom: 0 }}>
          <button className={status === "open" ? "active" : ""} onClick={() => { setStatus("open"); setPage(1); }}>To do</button>
          <button className={status === "resolved" ? "active" : ""} onClick={() => { setStatus("resolved"); setPage(1); }}>Handled</button>
        </div>
        <span className="muted small" style={{ maxWidth: 520 }}>
          Customers asked these on WhatsApp, email or a call and the assistant had no answer. Add a document that covers
          the question, then mark it handled.
        </span>
      </div>
      <ErrorBox error={error || act.error} />
      {isLoading ? <Loading /> : !data?.items.length ? (
        <Empty>{status === "open" ? "Nothing waiting: the assistant has answered everything it was asked." : "Nothing marked handled yet."}</Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Question</th><th>Channel</th><th>Domain</th><th>Why</th><th>Asked</th><th /></tr></thead>
              <tbody>
                {data.items.map((g) => (
                  <tr key={g.id}>
                    <td className="cell-wrap" style={{ maxWidth: 460 }}>{g.question}</td>
                    <td><Badge value={g.channel} /></td>
                    <td>{g.domain?.name ?? "—"}</td>
                    <td className="small muted cell-wrap">{WHY[g.reason] ?? g.reason}</td>
                    <td className="small nowrap">{fmtDate(g.created_at)}</td>
                    <td>
                      <div className="cell-actions">
                        {g.conversation_id && <Link className="btn ghost sm" to={`/conversations/${g.conversation_id}`}>Open</Link>}
                        {manager && (
                          <button className="btn ghost sm" disabled={act.isPending}
                            onClick={() => act.mutate({ id: g.id, action: g.status === "open" ? "resolve" : "reopen" })}>
                            {g.status === "open" ? "Mark handled" : "Reopen"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}
    </>
  );
}
