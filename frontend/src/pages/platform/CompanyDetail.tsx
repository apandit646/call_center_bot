import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { Company, Credentials } from "../../api/types";
import {
  CredentialsBox, IntegrationFields, LimitFields, ProfileFields, integrationsOf, integrationsPayload, limitsOf,
  limitsPayload, profileOf, profilePayload, type IntegrationForm, type LimitsForm, type ProfileForm,
} from "../../components/CompanyFields";
import { Badge, ErrorBox, Loading, PageHeader, UsageMeter, fmtDate } from "../../components/ui";

type Tab = "overview" | "profile" | "channels";

export default function CompanyDetail() {
  const { id } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [profile, setProfile] = useState<ProfileForm>(profileOf());
  const [limits, setLimits] = useState<LimitsForm>(limitsOf());
  const [channels, setChannels] = useState<IntegrationForm>(integrationsOf());
  const [email, setEmail] = useState("");

  const { data: company, isLoading, error } = useQuery({
    queryKey: ["company", id],
    queryFn: async () => (await api.get<Company>(`/companies/${id}`)).data,
  });
  useEffect(() => {
    if (!company) return;
    setProfile(profileOf(company));
    setLimits(limitsOf(company));
    setChannels(integrationsOf(company));
    setEmail(company.email ?? "");
  }, [company]);

  const save = useMutation({
    mutationFn: async (body: Record<string, unknown>) => (await api.patch<Company>(`/companies/${id}`, body)).data,
    onSuccess: (c) => {
      qc.setQueryData(["company", id], c);
      qc.invalidateQueries({ queryKey: ["companies"] });
      qc.invalidateQueries({ queryKey: ["platform-summary"] });
    },
  });
  const login = useMutation({
    mutationFn: async () => (await api.post<Credentials>(`/companies/${id}/credentials`, {})).data,
  });

  if (isLoading) return <Loading />;
  if (error || !company) return <ErrorBox error={error} />;
  const u = company.usage;

  return (
    <>
      <PageHeader
        title={<>{company.name} <Badge value={company.is_active ? "active" : "suspended"} /></>}
        subtitle={`${company.email ?? "No email set"} · registered ${fmtDate(company.created_at)}`}
        actions={
          <>
            <button className="btn ghost" disabled={login.isPending}
              onClick={() => confirm("Generate a new password for this company's admin? The old password stops working.") && login.mutate()}>
              {login.isPending ? "Generating…" : "Generate new login"}
            </button>
            {company.is_active ? (
              <button className="btn danger" disabled={save.isPending}
                onClick={() => confirm(`Suspend ${company.name}? Its users can't sign in and its campaigns stop calling.`) && save.mutate({ is_active: false })}>
                Suspend
              </button>
            ) : (
              <button className="btn" disabled={save.isPending} onClick={() => save.mutate({ is_active: true })}>Re-activate</button>
            )}
          </>
        }
      />
      <ErrorBox error={save.error || login.error} />
      {login.data && <CredentialsBox credentials={login.data} />}
      {save.isSuccess && <div className="success">Saved.</div>}

      <div className="tabs">
        {(["overview", "profile", "channels"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t === "overview" ? "Usage & limits" : t === "profile" ? "Profile" : "Twilio & WhatsApp"}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          {u && (
            <div className="grid grid-4" style={{ marginBottom: 18 }}>
              <UsageMeter label="Campaigns" used={u.campaigns} limit={u.max_campaigns} />
              <UsageMeter label="Calls today" used={u.calls_today} limit={u.daily_call_limit} hint={`Resets at midnight, ${company.timezone}`} />
              <UsageMeter label="Calls right now" used={u.active_calls} limit={u.max_concurrent_calls} />
              <UsageMeter label="Users" used={u.users} limit={null} />
            </div>
          )}
          <div className="card form">
            <h2>Limits</h2>
            <LimitFields form={limits} set={(p) => setLimits((f) => ({ ...f, ...p }))} />
            <div><button className="btn" disabled={save.isPending} onClick={() => save.mutate(limitsPayload(limits))}>Save limits</button></div>
          </div>
        </>
      )}

      {tab === "profile" && (
        <div className="card form">
          <h2>Company details</h2>
          <ProfileFields form={profile} set={(p) => setProfile((f) => ({ ...f, ...p }))} />
          <label>
            Company email <span className="hint">— contact address; also the login used when you generate a new login</span>
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="admin@acme.example" />
          </label>
          <div>
            <button className="btn" disabled={save.isPending || !profile.name.trim()}
              onClick={() => save.mutate({ ...profilePayload(profile), email: email.trim() || null })}>Save profile</button>
          </div>
        </div>
      )}

      {tab === "channels" && (
        <div className="card form">
          <h2>Twilio &amp; WhatsApp accounts</h2>
          <span className="hint">The company can set these itself under Company Settings. You can also set them here on its behalf.</span>
          <IntegrationFields company={company} form={channels} set={(p) => setChannels((f) => ({ ...f, ...p }))} />
          <div><button className="btn" disabled={save.isPending} onClick={() => save.mutate(integrationsPayload(channels))}>Save accounts</button></div>
        </div>
      )}
    </>
  );
}
