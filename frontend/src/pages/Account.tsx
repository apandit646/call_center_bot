import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Badge, ErrorBox, PageHeader } from "../components/ui";

export default function Account() {
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmPw, setConfirmPw] = useState("");

  const change = useMutation({
    mutationFn: async () => api.post("/auth/change-password", { current_password: current, new_password: next }),
    onSuccess: () => { setCurrent(""); setNext(""); setConfirmPw(""); },
  });

  return (
    <>
      <PageHeader title="My account" />
      <div className="grid grid-2">
        <div className="card">
          <h2>Profile</h2>
          <p>{user?.full_name}</p>
          <p className="muted">{user?.email}</p>
          <p className="muted">{user?.company ? user.company.name : "Platform administrator"}</p>
          {user && <Badge value={user.role} />}
        </div>
        <form className="card form" onSubmit={(e) => { e.preventDefault(); change.mutate(); }}>
          <h2>Change password</h2>
          <ErrorBox error={change.error} />
          {change.isSuccess && <div className="success">Password updated.</div>}
          <label>Current password<input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>
          <label>New password<input type="password" minLength={8} value={next} onChange={(e) => setNext(e.target.value)} required /></label>
          <label>Confirm new password<input type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} required /></label>
          {confirmPw && next !== confirmPw && <div className="small" style={{ color: "var(--danger)" }}>Passwords don't match</div>}
          <button className="btn" disabled={change.isPending || next.length < 8 || next !== confirmPw}>Update password</button>
        </form>
      </div>
    </>
  );
}
