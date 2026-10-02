// Form sections shared by the platform admin's company pages and the company's own settings page.
import { useEffect, useState } from "react";
import type { Company, Credentials } from "../api/types";
import { IconCheck, IconKey } from "./Icons";
import { Badge } from "./ui";

export interface ProfileForm {
  name: string;
  phone: string;
  website: string;
  industry: string;
  address: string;
  description: string;
  products_services: string;
  timezone: string;
}

export interface LimitsForm {
  max_campaigns: string; // blank = unlimited
  daily_call_limit: string;
  max_concurrent_calls: string;
}

export interface IntegrationForm {
  twilio_account_sid: string;
  twilio_auth_token: string; // blank = keep the saved one
  twilio_from_number: string;
  whatsapp_phone_number_id: string;
  whatsapp_access_token: string;
  whatsapp_app_secret: string;
}

export const profileOf = (c?: Company): ProfileForm => ({
  name: c?.name ?? "",
  phone: c?.phone ?? "",
  website: c?.website ?? "",
  industry: c?.industry ?? "",
  address: c?.address ?? "",
  description: c?.description ?? "",
  products_services: c?.products_services ?? "",
  timezone: c?.timezone ?? "Asia/Kolkata",
});

export const limitsOf = (c?: Company): LimitsForm => ({
  max_campaigns: c?.max_campaigns?.toString() ?? "",
  daily_call_limit: c?.daily_call_limit?.toString() ?? "",
  max_concurrent_calls: c?.max_concurrent_calls?.toString() ?? "",
});

export const integrationsOf = (c?: Company): IntegrationForm => ({
  twilio_account_sid: c?.twilio_account_sid ?? "",
  twilio_auth_token: "",
  twilio_from_number: c?.twilio_from_number ?? "",
  whatsapp_phone_number_id: c?.whatsapp_phone_number_id ?? "",
  whatsapp_access_token: "",
  whatsapp_app_secret: "",
});

/** A blank timezone means "keep the current one". */
export const profilePayload = (f: ProfileForm) => ({ ...f, timezone: f.timezone.trim() || null });

const numberOrNull = (v: string) => (v.trim() === "" ? null : Number(v));

export const limitsPayload = (f: LimitsForm) => ({
  max_campaigns: numberOrNull(f.max_campaigns),
  daily_call_limit: numberOrNull(f.daily_call_limit),
  max_concurrent_calls: numberOrNull(f.max_concurrent_calls),
});

/** Identifiers are always sent (blank clears them); secrets only when a new one was typed. */
export function integrationsPayload(f: IntegrationForm) {
  const secrets = Object.fromEntries(
    (["twilio_auth_token", "whatsapp_access_token", "whatsapp_app_secret"] as const)
      .filter((k) => f[k].trim() !== "")
      .map((k) => [k, f[k].trim()]),
  );
  return {
    twilio_account_sid: f.twilio_account_sid,
    twilio_from_number: f.twilio_from_number,
    whatsapp_phone_number_id: f.whatsapp_phone_number_id,
    ...secrets,
  };
}

type Setter<T> = (patch: Partial<T>) => void;

export function ProfileFields({ form, set, disabled }: { form: ProfileForm; set: Setter<ProfileForm>; disabled?: boolean }) {
  return (
    <>
      <div className="form-row">
        <label>Company name<input value={form.name} required disabled={disabled} onChange={(e) => set({ name: e.target.value })} placeholder="Acme Broadband Pvt Ltd" /></label>
        <label>Industry<input value={form.industry} disabled={disabled} onChange={(e) => set({ industry: e.target.value })} placeholder="Telecom, Insurance, Retail…" /></label>
      </div>
      <div className="form-row">
        <label>Phone<input value={form.phone} disabled={disabled} onChange={(e) => set({ phone: e.target.value })} placeholder="+91 1800 123 456" /></label>
        <label>Website<input value={form.website} disabled={disabled} onChange={(e) => set({ website: e.target.value })} placeholder="https://acme.example" /></label>
        <label>Timezone<input value={form.timezone} disabled={disabled} onChange={(e) => set({ timezone: e.target.value })} placeholder="Asia/Kolkata" /></label>
      </div>
      <label>Address<textarea value={form.address} disabled={disabled} onChange={(e) => set({ address: e.target.value })} placeholder="Registered office address" /></label>
      <label>
        About the company <span className="hint">— the AI says this when a customer asks "which company is this?" or "what do you do?"</span>
        <textarea value={form.description} disabled={disabled} onChange={(e) => set({ description: e.target.value })}
          placeholder="e.g. Acme Broadband is a home internet provider serving 12 cities since 2015. Support is open 9am–9pm, all days." />
      </label>
      <label>
        Products &amp; services <span className="hint">— what the company sells, in plain words. The AI never states anything that isn't written here.</span>
        <textarea style={{ minHeight: 110 }} value={form.products_services} disabled={disabled} onChange={(e) => set({ products_services: e.target.value })}
          placeholder={"e.g. Fibre plans: 100 Mbps (₹699/month), 300 Mbps (₹999/month). Free installation. 24-hour repair promise."} />
      </label>
    </>
  );
}

export function LimitFields({ form, set }: { form: LimitsForm; set: Setter<LimitsForm> }) {
  return (
    <>
      <div className="form-row">
        <label>Max campaigns<input type="number" min={0} value={form.max_campaigns} placeholder="Unlimited" onChange={(e) => set({ max_campaigns: e.target.value })} /></label>
        <label>Calls per day<input type="number" min={0} value={form.daily_call_limit} placeholder="Unlimited" onChange={(e) => set({ daily_call_limit: e.target.value })} /></label>
        <label>Calls at the same time<input type="number" min={1} max={200} value={form.max_concurrent_calls} placeholder="Unlimited" onChange={(e) => set({ max_concurrent_calls: e.target.value })} /></label>
      </div>
      <span className="hint">
        Leave a box empty for no limit. When the daily limit is reached, remaining calls wait and continue the next day (company timezone).
      </span>
    </>
  );
}

export function ChannelStatus({ connected }: { connected: boolean }) {
  return <Badge value={connected ? "connected" : "not_connected"} />;
}

export function IntegrationFields({ company, form, set }: { company: Company; form: IntegrationForm; set: Setter<IntegrationForm> }) {
  const saved = (isSet: boolean) => (isSet ? "Saved — leave blank to keep it" : "Not set");
  return (
    <>
      <div className="question-card form">
        <div className="channel-head"><strong>Voice calls — Twilio</strong><ChannelStatus connected={company.calls_connected} /></div>
        <span className="hint">
          Calls for this company are placed from this Twilio account and number, and billed by Twilio to that account.
          Find these in the Twilio Console under Account Info. Until all three are saved, campaigns cannot place calls.
        </span>
        <div className="form-row">
          <label>Account SID<input value={form.twilio_account_sid} onChange={(e) => set({ twilio_account_sid: e.target.value })} placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" autoComplete="off" /></label>
          <label>Auth token<input type="password" value={form.twilio_auth_token} onChange={(e) => set({ twilio_auth_token: e.target.value })} placeholder={saved(company.twilio_auth_token_set)} autoComplete="new-password" /></label>
          <label>Calling number<input value={form.twilio_from_number} onChange={(e) => set({ twilio_from_number: e.target.value })} placeholder="+14155550123" autoComplete="off" /></label>
        </div>
      </div>

      <div className="question-card form">
        <div className="channel-head"><strong>WhatsApp — Cloud API</strong><ChannelStatus connected={company.whatsapp_connected} /></div>
        <span className="hint">
          Messages sent to this WhatsApp number are answered from this company's knowledge base only. All three values
          are required: until they are saved, WhatsApp is off for this company. In Meta's developer
          console, set the webhook callback URL to <code>{company.whatsapp_webhook_url}</code> and subscribe to <code>messages</code>.
        </span>
        <div className="form-row">
          <label>Phone number ID<input value={form.whatsapp_phone_number_id} onChange={(e) => set({ whatsapp_phone_number_id: e.target.value })} placeholder="e.g. 106540352242922" autoComplete="off" /></label>
          <label>Access token<input type="password" value={form.whatsapp_access_token} onChange={(e) => set({ whatsapp_access_token: e.target.value })} placeholder={saved(company.whatsapp_access_token_set)} autoComplete="new-password" /></label>
          <label>App secret <span className="hint">(from your Meta app's Basic settings)</span>
            <input type="password" value={form.whatsapp_app_secret} onChange={(e) => set({ whatsapp_app_secret: e.target.value })} placeholder={saved(company.whatsapp_app_secret_set)} autoComplete="new-password" />
          </label>
        </div>
      </div>
      <span className="hint">Tokens are stored encrypted and are never shown again. To disconnect a channel, clear its Account SID or Phone number ID and save.</span>
    </>
  );
}

const INBOUND_LANGUAGES = [
  { code: "en-IN", label: "English", greeting: "Hello, thank you for calling {company}. I am the virtual assistant." },
  { code: "hi-IN", label: "Hindi", greeting: "नमस्ते, {company} को call करने के लिए धन्यवाद। मैं virtual assistant हूँ।" },
];

/** Inbound support calls on the company's Twilio number: the switch, the starting language and the greeting. */
export function InboundCard({ company, onSave, saving }: {
  company: Company; onSave: (body: Record<string, unknown>) => void; saving?: boolean;
}) {
  const [enabled, setEnabled] = useState(company.inbound_enabled);
  const [language, setLanguage] = useState(company.inbound_language);
  const [greeting, setGreeting] = useState(company.inbound_greeting ?? "");
  useEffect(() => {
    setEnabled(company.inbound_enabled);
    setLanguage(company.inbound_language);
    setGreeting(company.inbound_greeting ?? "");
  }, [company]);
  // What has to be fixed before inbound can be switched on (no Twilio account, not in the plan, API not public).
  const blocked = company.inbound_problem;
  const fallback = INBOUND_LANGUAGES.find((l) => l.code === language) ?? INBOUND_LANGUAGES[0];

  return (
    <div className="card form">
      <div className="channel-head">
        <h2 style={{ margin: 0 }}>Inbound calls</h2>
        <Badge value={company.inbound_active ? "on" : "off"} />
      </div>
      <span className="hint">
        When a customer calls {company.twilio_from_number ?? "your Twilio number"}, the AI agent answers. It uses the same knowledge base as
        WhatsApp, knows the tickets that caller already raised, can add to them, and raises a new ticket when it cannot help.
      </span>
      {blocked && <div className="error" role="status">{blocked}</div>}
      <label className="checkbox">
        <input type="checkbox" checked={enabled} disabled={!!blocked} onChange={(e) => setEnabled(e.target.checked)} />
        Answer incoming calls with the AI agent
      </label>
      <div className="form-row">
        <label>
          Calls start in
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            {INBOUND_LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
          </select>
        </label>
      </div>
      <span className="hint">The call then follows the caller: if they speak Hindi it continues in Hindi, and back to English if they switch.</span>
      <label>
        Greeting <span className="hint">— leave blank to use the standard greeting</span>
        <textarea value={greeting} onChange={(e) => setGreeting(e.target.value)} placeholder={fallback.greeting.replace("{company}", company.name)} />
      </label>
      <div>
        <button className="btn" disabled={saving} onClick={() => onSave({ inbound_enabled: blocked ? false : enabled, inbound_language: language, inbound_greeting: greeting })}>
          Save inbound settings
        </button>
      </div>
      <span className="hint">
        Switching this on sets the "a call comes in" address on your Twilio number automatically; switching it off removes it. Nothing to copy into Twilio.
      </span>
    </div>
  );
}

/** A generated login. The password exists only in this response, so it must be copied now. */
export function CredentialsBox({ credentials }: { credentials: Credentials }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(`Email: ${credentials.email}\nPassword: ${credentials.password}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="secret-box" role="status">
      <div className="row"><IconKey /><strong>Company login generated</strong></div>
      <div className="secret-row"><span className="hint">Email</span><code>{credentials.email}</code><span /></div>
      <div className="secret-row"><span className="hint">Password</span><code>{credentials.password}</code>
        <button type="button" className="btn ghost sm" onClick={copy}>{copied ? <><IconCheck size={14} /> Copied</> : "Copy both"}</button>
      </div>
      <span className="small">Copy it now and send it to the company. For security the password is not stored and cannot be shown again; you can generate a new one at any time.</span>
    </div>
  );
}
