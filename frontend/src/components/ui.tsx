import type { ReactNode } from "react";
import { errorMessage } from "../api/client";

const TONE: Record<string, string> = {
  open: "blue", in_progress: "amber", pending_customer: "amber", resolved: "green", closed: "grey",
  escalated: "red", low: "grey", medium: "blue", high: "amber", urgent: "red",
  draft: "grey", scheduled: "blue", running: "green", paused: "amber", completed: "green", cancelled: "grey",
  pending: "grey", processing: "amber", ready: "green", failed: "red",
  initiated: "blue", ringing: "blue", no_answer: "amber", busy: "amber", canceled: "grey",
  answered: "green", unclear: "amber", skipped: "grey", do_not_call: "grey",
  preview: "blue", confirmed: "green", discarded: "grey",
  admin: "red", supervisor: "amber", agent: "blue",
};

export function Badge({ value }: { value: string }) {
  return <span className={`badge ${TONE[value] ?? "grey"}`}>{value.replace(/_/g, " ")}</span>;
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="error">{errorMessage(error)}</div>;
}

export function Loading() {
  return <div className="muted pad">Loading…</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty muted">{children}</div>;
}

export function Pagination({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="pagination">
      <span className="muted">{total} total</span>
      <button className="btn ghost" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Prev</button>
      <span>Page {page} of {pages}</span>
      <button className="btn ghost" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next ›</button>
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export const fmtDate = (v: string | null | undefined) => (v ? new Date(v).toLocaleString() : "—");

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
