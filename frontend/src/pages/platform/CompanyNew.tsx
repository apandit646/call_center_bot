import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Company, Credentials } from "../../api/types";
import {
  CredentialsBox, LimitFields, ProfileFields, limitsOf, limitsPayload, profileOf, profilePayload,
  type LimitsForm, type ProfileForm,
} from "../../components/CompanyFields";
import { ErrorBox, PageHeader } from "../../components/ui";

export default function CompanyNew() {
  const qc = useQueryClient();
  const [profile, setProfile] = useState<ProfileForm>(profileOf());
  const [limits, setLimits] = useState<LimitsForm>({ ...limitsOf(), max_campaigns: "5", daily_call_limit: "500" });
  const [email, setEmail] = useState("");
  const [adminName, setAdminName] = useState("");

  const register = useMutation({
    mutationFn: async () =>
      (
        await api.post<{ company: Company; credentials: Credentials }>("/companies", {
          ...profilePayload(profile), ...limitsPayload(limits), email, admin_name: adminName || null,
        })
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["companies"] });
      qc.invalidateQueries({ queryKey: ["platform-summary"] });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    register.mutate();
  };

  // Registered: the generated password exists only in this response, so stay here until it is copied.
  if (register.data) {
    const { company, credentials } = register.data;
    return (
      <>
        <PageHeader title={`${company.name} is registered`} subtitle="Send this login to the company. They sign in on the same page as you and see only their own portal." />
        <CredentialsBox credentials={credentials} />
        <div className="card">
          <h2>What the company does next</h2>
          <ol className="muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
            <li>Signs in and changes the password under My Account.</li>
            <li>Opens Company Settings and connects its own Twilio number and WhatsApp number.</li>
            <li>Uploads its knowledge base, then creates campaigns within the limits you set.</li>
          </ol>
        </div>
        <div className="row">
          <Link className="btn" to={`/companies/${company.id}`}>Open company</Link>
          <Link className="btn ghost" to="/">Back to companies</Link>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Register a company" subtitle="Creates the company, its private workspace and an admin login with a generated password." />
      <form className="form" onSubmit={submit}>
        <ErrorBox error={register.error} />
        <div className="card form">
          <h2>Company details</h2>
          <ProfileFields form={profile} set={(p) => setProfile((f) => ({ ...f, ...p }))} />
        </div>
        <div className="card form">
          <h2>Company login</h2>
          <div className="form-row">
            <label>
              Company email <span className="hint">— this becomes the login</span>
              <input type="email" value={email} required onChange={(e) => setEmail(e.target.value)} placeholder="admin@acme.example" />
            </label>
            <label>Admin's name <span className="hint">(optional)</span>
              <input value={adminName} onChange={(e) => setAdminName(e.target.value)} placeholder="Priya Sharma" />
            </label>
          </div>
          <span className="hint">A strong password is generated when you register and shown to you once.</span>
        </div>
        <div className="card form">
          <h2>Limits</h2>
          <LimitFields form={limits} set={(p) => setLimits((f) => ({ ...f, ...p }))} />
        </div>
        <div className="row">
          <button className="btn" disabled={register.isPending || !profile.name.trim() || !email.trim()}>
            {register.isPending ? "Registering…" : "Register company & generate login"}
          </button>
          <Link className="btn ghost" to="/">Cancel</Link>
        </div>
      </form>
    </>
  );
}
