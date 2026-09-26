import type { ReactNode } from "react";
import { errorMessage } from "../api/client";
import { IconAlert } from "./Icons";

export type Tone = "blue" | "green" | "amber" | "red" | "grey" | "violet";

const TONE: Record<string, Tone> = {
  open: "blue", in_progress: "amber", pending_customer: "amber", resolved: "green", closed: "grey",
  escalated: "red", low: "grey", medium: "blue", high: "amber", urgent: "red",
  draft: "grey", scheduled: "blue", running: "green", paused: "amber", completed: "green", cancelled: "grey",
  pending: "grey", processing: "amber", ready: "green", failed: "red",
  initiated: "blue", ringing: "blue", no_answer: "amber", busy: "amber", canceled: "grey",
  answered: "green", unclear: "amber", skipped: "grey", do_not_call: "grey",
  preview: "blue", confirmed: "green", discarded: "grey",
  admin: "violet", supervisor: "amber", agent: "blue",
};

export function Badge({ value, tone }: { value: string; tone?: Tone }) {
  return (
    <span className={`badge ${tone ?? TONE[value] ?? "grey"}`}>
      <span className="badge-dot" />
      {value.replace(/_/g, " ")}
    </span>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div className="page-header-text">
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      <IconAlert size={16} />
      <span>{errorMessage(error)}</span>
    </div>
  );
}

export function Loading() {
  return (
    <div className="loading">
      <span className="spinner" /> Loading…
    </div>
  );
}

export function Empty({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="empty">
      {icon && <div className="empty-icon">{icon}</div>}
      <div className="muted">{children}</div>
    </div>
  );
}

export function Pagination({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="pagination">
      <span className="muted small">{total.toLocaleString()} total</span>
      <div className="row">
        <button className="btn ghost sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Prev</button>
        <span className="small">Page {page} of {pages}</span>
        <button className="btn ghost sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next ›</button>
      </div>
    </div>
  );
}

export function Stat({ label, value, hint, icon, tone = "blue" }: {
  label: string; value: ReactNode; hint?: string; icon?: ReactNode; tone?: Tone;
}) {
  return (
    <div className="stat">
      <div className="stat-top">
        <div className="stat-label">{label}</div>
        {icon && <div className={`stat-icon ${tone}`}>{icon}</div>}
      </div>
      <div className="stat-value">{value}</div>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Avatar({ name, size = 32 }: { name: string | null | undefined; size?: number }) {
  const initials = (name ?? "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
  return <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.4 }}>{initials}</span>;
}

export const fmtDate = (v: string | null | undefined) =>
  v ? new Date(v).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
