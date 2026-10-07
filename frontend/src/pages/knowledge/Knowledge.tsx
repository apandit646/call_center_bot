import { useState, type FormEvent } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Domain, KnowledgeDocument, Page, SearchHit } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { cleanParams, useDomains } from "../../hooks";
import { Badge, Empty, ErrorBox, Loading, Modal, PageHeader, Pagination, fmtDate } from "../../components/ui";
import Unanswered from "./Unanswered";

export default function Knowledge() {
  const { hasRole } = useAuth();
  const manager = hasRole("admin", "supervisor");
  const [tab, setTab] = useState<"documents" | "gaps" | "search" | "domains">("documents");

  return (
    <>
      <PageHeader
        title="Knowledge Base"
        subtitle="Domain-specific documents used by the WhatsApp RAG assistant. Billing questions only search billing documents."
      />
      <div className="tabs">
        <button className={tab === "documents" ? "active" : ""} onClick={() => setTab("documents")}>Documents</button>
        <button className={tab === "gaps" ? "active" : ""} onClick={() => setTab("gaps")}>Unanswered questions</button>
        <button className={tab === "search" ? "active" : ""} onClick={() => setTab("search")}>Test retrieval</button>
        {manager && <button className={tab === "domains" ? "active" : ""} onClick={() => setTab("domains")}>Domains</button>}
      </div>
      {tab === "documents" && <Documents manager={manager} />}
      {tab === "gaps" && <Unanswered manager={manager} />}
      {tab === "search" && <SearchTester />}
      {tab === "domains" && manager && <Domains />}
    </>
  );
}

function Documents({ manager }: { manager: boolean }) {
  const qc = useQueryClient();
  const { data: domains } = useDomains();
  const [page, setPage] = useState(1);
  const [domainId, setDomainId] = useState("");
  const [adding, setAdding] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["documents", page, domainId],
    queryFn: async () =>
      (await api.get<Page<KnowledgeDocument>>("/knowledge/documents", { params: cleanParams({ page, domain_id: domainId }) })).data,
    placeholderData: keepPreviousData,
    // Poll while anything is still being embedded.
    refetchInterval: (q) => (q.state.data?.items.some((d) => d.status === "pending" || d.status === "processing") ? 3_000 : false),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["documents"] });
  const action = useMutation({
    mutationFn: async ({ id, kind }: { id: string; kind: "delete" | "reprocess" }) =>
      kind === "delete" ? api.delete(`/knowledge/documents/${id}`) : api.post(`/knowledge/documents/${id}/reprocess`),
    onSuccess: refresh,
  });

  return (
    <>
      <div className="filters spread row">
        <select value={domainId} onChange={(e) => { setDomainId(e.target.value); setPage(1); }}>
          <option value="">All domains</option>
          {domains?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        {manager && <button className="btn" onClick={() => setAdding(true)}>+ Add document</button>}
      </div>
      <ErrorBox error={error || action.error} />
      {isLoading ? <Loading /> : !data?.items.length ? <Empty>No documents yet. Add FAQs and policies per domain.</Empty> : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Title</th><th>Domain</th><th>Status</th><th>Chunks</th><th>Added</th>{manager && <th />}</tr></thead>
              <tbody>
                {data.items.map((d) => (
                  <tr key={d.id}>
                    <td className="cell-wrap">
                      {d.title}
                      {d.source_filename && d.source_filename !== `${d.title}.pdf` && d.source_filename !== `${d.title}.txt` && (
                        <div className="small muted">{d.source_filename}</div>
                      )}
                    </td>
                    <td>{d.domain.name}</td>
                    <td className="cell-wrap">
                      <Badge value={d.status} />
                      {d.status === "failed" && d.error && <div className="small cell-error" title={d.error}>{shortError(d.error)}</div>}
                    </td>
                    <td>{d.chunk_count}</td>
                    <td className="small nowrap">{fmtDate(d.created_at)}</td>
                    {manager && (
                      <td>
                        <div className="cell-actions">
                          <button className="btn ghost sm" onClick={() => action.mutate({ id: d.id, kind: "reprocess" })}>Re-index</button>
                          <button className="btn danger sm"
                            onClick={() => confirm(`Delete "${d.title}"?`) && action.mutate({ id: d.id, kind: "delete" })}>
                            Delete
                          </button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}
      {adding && <AddDocument domains={domains ?? []} onClose={() => setAdding(false)} onDone={() => { setAdding(false); refresh(); }} />}
    </>
  );
}

/** A stored indexing error as one plain sentence (older rows hold the raw technical error). */
function shortError(error: string): string {
  if (/429|RESOURCE_EXHAUSTED|quota/i.test(error)) return "The AI service's usage limit was reached while indexing. Wait a minute, then press Re-index.";
  const first = error.split(/(?<=[.!?])\s|\n|\{/)[0].trim();
  return first.length > 180 ? `${first.slice(0, 177)}…` : first || "This document could not be indexed. Press Re-index to try again.";
}

function AddDocument({ domains, onClose, onDone }: { domains: Domain[]; onClose: () => void; onDone: () => void }) {
  const [mode, setMode] = useState<"file" | "text">("file");
  const [domainId, setDomainId] = useState(domains[0]?.id ?? "");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [content, setContent] = useState("");

  const save = useMutation({
    mutationFn: async () => {
      if (mode === "file") {
        const form = new FormData();
        form.append("domain_id", domainId);
        if (title) form.append("title", title);
        form.append("file", file!);
        return api.post("/knowledge/documents/upload", form);
      }
      return api.post("/knowledge/documents/text", { domain_id: domainId, title, content });
    },
    onSuccess: onDone,
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  const valid = domainId && (mode === "file" ? !!file : title.trim() && content.trim().length >= 10);

  return (
    <Modal title="Add knowledge document" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <ErrorBox error={save.error} />
        <div className="tabs">
          <button type="button" className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}>Upload file</button>
          <button type="button" className={mode === "text" ? "active" : ""} onClick={() => setMode("text")}>Paste text</button>
        </div>
        <label>
          Domain
          <select value={domainId} onChange={(e) => setDomainId(e.target.value)} required>
            {domains.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>
        <label>
          Title {mode === "file" && <span className="muted small">(optional — defaults to file name)</span>}
          <input value={title} onChange={(e) => setTitle(e.target.value)} required={mode === "text"} />
        </label>
        {mode === "file" ? (
          <label>
            File (PDF, DOCX, TXT, MD — max 20 MB)
            <input type="file" accept=".pdf,.docx,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
        ) : (
          <label>
            Content
            <textarea style={{ minHeight: 200 }} value={content} onChange={(e) => setContent(e.target.value)} />
          </label>
        )}
        <button className="btn" disabled={!valid || save.isPending}>{save.isPending ? "Uploading…" : "Add & index"}</button>
      </form>
    </Modal>
  );
}

function SearchTester() {
  const { data: domains } = useDomains();
  const [domainId, setDomainId] = useState("");
  const [query, setQuery] = useState("");
  const search = useMutation({
    mutationFn: async () =>
      (await api.post<SearchHit[]>("/knowledge/search", { domain_id: domainId || domains?.[0]?.id, query, top_k: 5 })).data,
  });

  return (
    <div className="card form">
      <p className="muted">See which chunks the assistant would retrieve for a customer question in a given domain.</p>
      <div className="form-row">
        <select value={domainId} onChange={(e) => setDomainId(e.target.value)}>
          {domains?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        <input placeholder="e.g. How long does a refund take?" value={query} onChange={(e) => setQuery(e.target.value)} />
        <button className="btn" onClick={() => search.mutate()} disabled={query.trim().length < 2 || search.isPending}>Search</button>
      </div>
      <ErrorBox error={search.error} />
      {search.data && (search.data.length === 0 ? <Empty>No chunks in this domain yet.</Empty> : search.data.map((h) => (
        <div key={h.chunk_id} className="question-card">
          <div className="row spread">
            <strong>{h.document_title}</strong>
            <span className="small muted">similarity {h.similarity.toFixed(3)}</span>
          </div>
          <pre className="small">{h.content}</pre>
        </div>
      )))}
    </div>
  );
}

function Domains() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["domains", "all"],
    queryFn: async () => (await api.get<Domain[]>("/domains", { params: { include_inactive: true } })).data,
  });
  const [editing, setEditing] = useState<Partial<Domain> | null>(null);

  const save = useMutation({
    mutationFn: async (d: Partial<Domain>) => {
      const body = {
        name: d.name, description: d.description,
        keywords: typeof d.keywords === "string" ? (d.keywords as string).split(",").map((k) => k.trim()).filter(Boolean) : d.keywords,
        ...(d.id ? { is_active: d.is_active } : { code: d.code }),
      };
      return d.id ? api.patch(`/domains/${d.id}`, body) : api.post("/domains", body);
    },
    onSuccess: () => {
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["domains"] });
    },
  });

  if (isLoading) return <Loading />;
  return (
    <>
      <div className="filters"><button className="btn" onClick={() => setEditing({ keywords: [], is_active: true })}>+ New domain</button></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Code</th><th>Name</th><th>Description</th><th>Keywords</th><th>Active</th><th /></tr></thead>
          <tbody>
            {data?.map((d) => (
              <tr key={d.id}>
                <td><code>{d.code}</code></td><td>{d.name}</td><td className="small">{d.description}</td>
                <td className="small">{d.keywords.join(", ")}</td>
                <td>{d.is_active ? "Yes" : "No"}</td>
                <td><button className="btn ghost sm" onClick={() => setEditing(d)}>Edit</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {editing && (
        <Modal title={editing.id ? `Edit ${editing.name}` : "New domain"} onClose={() => setEditing(null)}>
          <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(editing); }}>
            <ErrorBox error={save.error} />
            {!editing.id && (
              <label>Code (lowercase, used by the classifier)
                <input value={editing.code ?? ""} onChange={(e) => setEditing({ ...editing, code: e.target.value })} required pattern="[a-z0-9_]{2,64}" />
              </label>
            )}
            <label>Name<input value={editing.name ?? ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} required /></label>
            <label>Description (helps Gemini route questions)
              <textarea value={editing.description ?? ""} onChange={(e) => setEditing({ ...editing, description: e.target.value })} />
            </label>
            <label>Keywords (comma separated)
              <input
                defaultValue={(editing.keywords ?? []).join(", ")}
                onChange={(e) => setEditing({ ...editing, keywords: e.target.value as unknown as string[] })}
              />
            </label>
            {editing.id && (
              <label className="checkbox">
                <input type="checkbox" checked={!!editing.is_active} onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} /> Active
              </label>
            )}
            <button className="btn" disabled={save.isPending}>Save</button>
          </form>
        </Modal>
      )}
    </>
  );
}
