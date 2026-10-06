import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Role } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { IconLogout } from "../components/Icons";
import { Avatar, Badge, ErrorBox, PageHeader, fmtDate } from "../components/ui";

const ROLE_ACCESS: Record<Role, string> = {
  super_admin: "You manage the platform: register companies, set their limits and issue their logins.",
  admin: "Full access to your company: settings, users, campaigns, knowledge base, tickets and conversations.",
  supervisor: "You run the day-to-day work: campaigns, users, knowledge base, and every ticket and conversation.",
  agent: "You work the tickets assigned to you and reply to customers on WhatsApp.",
};

/** One line of the "what a good password needs" list, ticked as the person types. */
function Rule({ ok, children }: { ok: boolean; children: string }) {
  return (
    <li className={ok ? "ok" : ""}>
      <span aria-hidden="true">{ok ? "✓" : "•"}</span> {children}
    </li>
  );
}

export default function Account() {
  const { user, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [show, setShow] = useState(false);

  const change = useMutation({
    mutationFn: async () => api.post("/auth/change-password", { current_password: current, new_password: next }),
    onSuccess: () => { setCurrent(""); setNext(""); setConfirmPw(""); },
  });

  const longEnough = next.length >= 8;
  const different = !!next && next !== current;
  const matches = !!confirmPw && next === confirmPw;
  const type = show ? "text" : "password";
  if (!user) return null;

  return (
    <>
      <PageHeader title="My account" subtitle="Your sign-in details and password." />
      <div className="grid grid-2 account">
        <div className="card">
          <div className="profile-head">
            <Avatar name={user.full_name} size={72} />
            <div className="profile-name">
              <h2>{user.full_name}</h2>
              <div className="muted">{user.email}</div>
              <div className="row" style={{ marginTop: 8 }}>
                <Badge value={user.role} />
                <Badge value={user.is_active ? "active" : "suspended"} />
              </div>
            </div>
          </div>

          <dl className="meta-list profile-facts">
            <dt>{user.company ? "Company" : "Workspace"}</dt><dd>{user.company ? user.company.name : "Platform administrator"}</dd>
            <dt>Sign-in email</dt><dd>{user.email}</dd>
            <dt>Member since</dt><dd>{fmtDate(user.created_at)}</dd>
            <dt>Last sign-in</dt><dd>{fmtDate(user.last_login_at)}</dd>
            {user.agent_profile && (
              <>
                <dt>Handles</dt>
                <dd>{user.agent_profile.domain_codes.length ? user.agent_profile.domain_codes.join(", ") : "All domains"}</dd>
                <dt>Open tickets limit</dt><dd>{user.agent_profile.max_open_tickets}</dd>
              </>
            )}
          </dl>

          <div className="note-box">
            <strong>What you can do</strong>
            <span>{ROLE_ACCESS[user.role]}</span>
          </div>
          <p className="hint" style={{ margin: "14px 0 0" }}>
            To change your name, email or role, ask {user.role === "admin" || user.role === "super_admin" ? "the platform administrator" : "your company admin"}.
          </p>

          <div style={{ marginTop: 18 }}>
            <button type="button" className="btn ghost" onClick={logout}><IconLogout size={16} /> Sign out</button>
          </div>
        </div>

        <form className="card form" onSubmit={(e) => { e.preventDefault(); change.mutate(); }}>
          <div>
            <h2 style={{ marginBottom: 4 }}>Change password</h2>
            <div className="hint">You stay signed in on this device after changing it.</div>
          </div>
          <ErrorBox error={change.error} />
          {change.isSuccess && <div className="success">Password updated. Use the new one the next time you sign in.</div>}
          <label>
            Current password
            <input type={type} value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
          </label>
          <label>
            New password
            <input type={type} minLength={8} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" required />
          </label>
          <label>
            Confirm new password
            <input type={type} value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} autoComplete="new-password" required />
          </label>
          <label className="checkbox">
            <input type="checkbox" checked={show} onChange={(e) => setShow(e.target.checked)} /> Show passwords
          </label>
          <ul className="rules" aria-label="Password requirements">
            <Rule ok={longEnough}>At least 8 characters</Rule>
            <Rule ok={different}>Different from your current password</Rule>
            <Rule ok={matches}>Both new passwords match</Rule>
          </ul>
          <div>
            <button className="btn" disabled={change.isPending || !current || !longEnough || !different || !matches}>
              {change.isPending ? "Updating…" : "Update password"}
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
