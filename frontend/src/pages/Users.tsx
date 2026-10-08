import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Page, Role, User } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useDomains } from "../hooks";
import { Badge, ErrorBox, Loading, Modal, PageHeader, Pagination, fmtDate } from "../components/ui";
import { Select } from "../components/Select";

interface FormState {
  id?: string;
  email: string;
  full_name: string;
  password: string;
  role: Role;
  is_active: boolean;
  domain_codes: string[];
  max_open_tickets: number;
  is_available: boolean;
}

const toForm = (u?: User): FormState => ({
  id: u?.id,
  email: u?.email ?? "",
  full_name: u?.full_name ?? "",
  password: "",
  role: u?.role ?? "agent",
  is_active: u?.is_active ?? true,
  domain_codes: u?.agent_profile?.domain_codes ?? [],
  max_open_tickets: u?.agent_profile?.max_open_tickets ?? 20,
  is_available: u?.agent_profile?.is_available ?? true,
});

export default function Users() {
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const isAdmin = hasRole("admin");
  const { data: domains } = useDomains();
  const [page, setPage] = useState(1);
  const [form, setForm] = useState<FormState | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["users", page],
    queryFn: async () => (await api.get<Page<User>>("/users", { params: { page } })).data,
    placeholderData: keepPreviousData,
  });

  const save = useMutation({
    mutationFn: async (f: FormState) => {
      const agent_profile = f.role === "agent"
        ? { domain_codes: f.domain_codes, max_open_tickets: f.max_open_tickets, is_available: f.is_available }
        : undefined;
      if (f.id) {
        return api.patch(`/users/${f.id}`, {
          full_name: f.full_name, role: f.role, is_active: f.is_active, password: f.password || undefined, agent_profile,
        });
      }
      return api.post("/users", { email: f.email, full_name: f.full_name, password: f.password, role: f.role, agent_profile });
    },
    onSuccess: () => {
      setForm(null);
      qc.invalidateQueries({ queryKey: ["users"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  const set = (patch: Partial<FormState>) => setForm((f) => (f ? { ...f, ...patch } : f));

  return (
    <>
      <PageHeader
        title="Users & Agents"
        subtitle="Agents receive escalated WhatsApp tickets automatically, filtered by the domains they handle"
        actions={isAdmin && <button className="btn" onClick={() => setForm(toForm())}>+ Add user</button>}
      />
      <ErrorBox error={error} />
      {isLoading ? <Loading /> : data && (
        <>
          <div className="table-wrap"><table>
            <thead><tr><th>Name</th><th>Role</th><th>Domains</th><th>Available</th><th>Active</th><th>Last login</th>{isAdmin && <th />}</tr></thead>
            <tbody>{data.items.map((u) => (
              <tr key={u.id}>
                <td>{u.full_name}<div className="small muted">{u.email}</div></td>
                <td><Badge value={u.role} /></td>
                <td className="small">{u.agent_profile ? (u.agent_profile.domain_codes.join(", ") || "All") : "—"}</td>
                <td>{u.agent_profile ? (u.agent_profile.is_available ? "Yes" : "No") : "—"}</td>
                <td>{u.is_active ? "Yes" : "No"}</td>
                <td className="small">{fmtDate(u.last_login_at)}</td>
                {isAdmin && <td><button className="btn ghost sm" onClick={() => setForm(toForm(u))}>Edit</button></td>}
              </tr>
            ))}</tbody>
          </table></div>
          <Pagination page={page} size={data.size} total={data.total} onPage={setPage} />
        </>
      )}

      {form && (
        <Modal title={form.id ? "Edit user" : "Add user"} onClose={() => setForm(null)}>
          <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(form); }}>
            <ErrorBox error={save.error} />
            <label>Email<input type="email" value={form.email} disabled={!!form.id} required onChange={(e) => set({ email: e.target.value })} /></label>
            <label>Full name<input value={form.full_name} required onChange={(e) => set({ full_name: e.target.value })} /></label>
            <label>
              {form.id ? "New password (leave blank to keep)" : "Password"}
              <input type="password" value={form.password} minLength={8} required={!form.id} onChange={(e) => set({ password: e.target.value })} />
            </label>
            <label>Role
              <Select value={form.role} onChange={(e) => set({ role: e.target.value as Role })}>
                <option value="agent">Agent</option><option value="supervisor">Supervisor</option><option value="admin">Admin</option>
              </Select>
            </label>
            {form.id && <label className="checkbox"><input type="checkbox" checked={form.is_active} onChange={(e) => set({ is_active: e.target.checked })} /> Active</label>}
            {form.role === "agent" && (
              <div className="question-card form">
                <strong>Agent profile</strong>
                <div className="small muted">Domains handled (none selected = all domains)</div>
                <div className="row">
                  {domains?.map((d) => (
                    <label key={d.code} className="checkbox">
                      <input type="checkbox" checked={form.domain_codes.includes(d.code)}
                        onChange={(e) => set({ domain_codes: e.target.checked ? [...form.domain_codes, d.code] : form.domain_codes.filter((c) => c !== d.code) })} />
                      {d.name}
                    </label>
                  ))}
                </div>
                <label>Max open tickets<input type="number" min={1} value={form.max_open_tickets} onChange={(e) => set({ max_open_tickets: +e.target.value })} /></label>
                <label className="checkbox"><input type="checkbox" checked={form.is_available} onChange={(e) => set({ is_available: e.target.checked })} /> Available for auto-assignment</label>
              </div>
            )}
            <button className="btn" disabled={save.isPending}>Save</button>
          </form>
        </Modal>
      )}
    </>
  );
}
