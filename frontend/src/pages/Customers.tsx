import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Customer, Page } from "../api/types";
import { cleanParams } from "../hooks";
import { Empty, ErrorBox, Loading, Modal, PageHeader, Pagination, fmtDate } from "../components/ui";

export default function Customers() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Partial<Customer> | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["customers", page, q],
    queryFn: async () => (await api.get<Page<Customer>>("/customers", { params: cleanParams({ page, q }) })).data,
    placeholderData: keepPreviousData,
  });

  const save = useMutation({
    mutationFn: async (c: Partial<Customer>) => {
      const body = { name: c.name || null, email: c.email || null, city: c.city || null };
      return c.id ? api.patch(`/customers/${c.id}`, body) : api.post("/customers", { ...body, phone: c.phone });
    },
    onSuccess: () => {
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["customers"] });
    },
  });

  return (
    <>
      <PageHeader
        title="Customers"
        subtitle="Shared by both workflows — created from WhatsApp chats and Excel imports"
        actions={<button className="btn" onClick={() => setEditing({})}>+ Add customer</button>}
      />
      <div className="filters">
        <input placeholder="Search name, phone or email…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      </div>
      <ErrorBox error={error} />
      {isLoading ? <Loading /> : !data?.items.length ? <Empty>No customers found.</Empty> : (
        <>
          <div className="table-wrap"><table>
            <thead><tr><th>Name</th><th>Phone</th><th>Email</th><th>City</th><th>WhatsApp</th><th>Created</th><th /></tr></thead>
            <tbody>{data.items.map((c) => (
              <tr key={c.id}>
                <td>{c.name ?? "—"}</td><td>{c.phone}</td><td>{c.email ?? "—"}</td><td>{c.city ?? "—"}</td>
                <td>{c.whatsapp_id ? "Yes" : "—"}</td><td className="small">{fmtDate(c.created_at)}</td>
                <td><button className="btn ghost sm" onClick={() => setEditing(c)}>Edit</button></td>
              </tr>
            ))}</tbody>
          </table></div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}
      {editing && (
        <Modal title={editing.id ? "Edit customer" : "Add customer"} onClose={() => setEditing(null)}>
          <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(editing); }}>
            <ErrorBox error={save.error} />
            <label>Phone<input value={editing.phone ?? ""} disabled={!!editing.id} required
              onChange={(e) => setEditing({ ...editing, phone: e.target.value })} placeholder="+919876543210" /></label>
            <label>Name<input value={editing.name ?? ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></label>
            <label>Email<input type="email" value={editing.email ?? ""} onChange={(e) => setEditing({ ...editing, email: e.target.value })} /></label>
            <label>City<input value={editing.city ?? ""} onChange={(e) => setEditing({ ...editing, city: e.target.value })} /></label>
            <button className="btn" disabled={save.isPending}>Save</button>
          </form>
        </Modal>
      )}
    </>
  );
}
