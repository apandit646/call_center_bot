import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Company } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import {
  IntegrationFields, ProfileFields, integrationsOf, integrationsPayload, profileOf, profilePayload,
  type IntegrationForm, type ProfileForm,
} from "../components/CompanyFields";
import { useCompany } from "../hooks";
import { ErrorBox, Loading, PageHeader, UsageMeter } from "../components/ui";

type Tab = "profile" | "channels" | "plan";

export default function CompanySettings() {
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const isAdmin = hasRole("admin");
  const [tab, setTab] = useState<Tab>("profile");
  const [profile, setProfile] = useState<ProfileForm>(profileOf());
  const [channels, setChannels] = useState<IntegrationForm>(integrationsOf());

  const { data: company, isLoading, error } = useCompany();
  useEffect(() => {
    if (!company) return;
    setProfile(profileOf(company));
    setChannels(integrationsOf(company));
  }, [company]);

  const save = useMutation({
    mutationFn: async (body: Record<string, unknown>) => (await api.patch<Company>("/company", body)).data,
    onSuccess: (c) => qc.setQueryData(["my-company"], c),
  });

  if (isLoading) return <Loading />;
  if (error || !company) return <ErrorBox error={error} />;
  const u = company.usage;
  const tabs: Tab[] = isAdmin ? ["profile", "channels", "plan"] : ["profile", "plan"];

  return (
    <>
      <PageHeader
        title="Company settings"
        subtitle="What your AI agents know about your company, and the phone and WhatsApp numbers they use."
      />
      <ErrorBox error={save.error} />
      {save.isSuccess && <div className="success">Saved.</div>}
      <div className="tabs">
        {tabs.map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {t === "profile" ? "Company profile" : t === "channels" ? "Twilio & WhatsApp" : "Plan & usage"}
          </button>
        ))}
      </div>

      {tab === "profile" && (
        <div className="card form">
          <h2>Company profile</h2>
          <span className="hint">
            The voice agent and the WhatsApp bot use this to answer questions about your company. They only state what is written here.
          </span>
          <ProfileFields form={profile} set={(p) => setProfile((f) => ({ ...f, ...p }))} disabled={!isAdmin} />
          {isAdmin
            ? <div><button className="btn" disabled={save.isPending || !profile.name.trim()} onClick={() => save.mutate(profilePayload(profile))}>Save profile</button></div>
            : <span className="hint">Only a company admin can edit the profile.</span>}
        </div>
      )}

      {tab === "channels" && isAdmin && (
        <div className="card form">
          <h2>Your phone and WhatsApp numbers</h2>
          <IntegrationFields company={company} form={channels} set={(p) => setChannels((f) => ({ ...f, ...p }))} />
          <div><button className="btn" disabled={save.isPending} onClick={() => save.mutate(integrationsPayload(channels))}>Save accounts</button></div>
        </div>
      )}

      {tab === "plan" && u && (
        <div className="card">
          <h2>Plan &amp; usage</h2>
          <p className="muted small">Limits are set by the platform administrator. Contact them to change your plan.</p>
          <div className="grid grid-4">
            <UsageMeter label="Campaigns" used={u.campaigns} limit={u.max_campaigns} />
            <UsageMeter label="Calls today" used={u.calls_today} limit={u.daily_call_limit} hint={`Resets at midnight, ${company.timezone}`} />
            <UsageMeter label="Calls right now" used={u.active_calls} limit={u.max_concurrent_calls} />
            <UsageMeter label="Users" used={u.users} limit={null} />
          </div>
        </div>
      )}
    </>
  );
}
