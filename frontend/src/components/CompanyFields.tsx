// Form sections shared by the platform admin's company pages and the company's own settings page.
import { useEffect, useState } from "react";
import type { Company, Credentials, TelephonyProvider } from "../api/types";
import { IconCheck, IconKey } from "./Icons";
import { Badge } from "./ui";
import { Select } from "./Select";

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
  telephony_provider: TelephonyProvider; // one calling provider at a time
  twilio_account_sid: string;
  twilio_auth_token: string; // blank = keep the saved one
  twilio_from_number: string;
  exotel_api_key: string;
  exotel_api_token: string;
  exotel_account_sid: string;
  exotel_subdomain: string;
  exotel_number: string;
  exotel_app_id: string;
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
  telephony_provider: c?.telephony_provider ?? "twilio",
  twilio_account_sid: c?.twilio_account_sid ?? "",
  twilio_auth_token: "",
  twilio_from_number: c?.twilio_from_number ?? "",
  exotel_api_key: c?.exotel_api_key ?? "",
  exotel_api_token: "",
  exotel_account_sid: c?.exotel_account_sid ?? "",
  exotel_subdomain: c?.exotel_subdomain ?? "api.in.exotel.com",
  exotel_number: c?.exotel_number ?? "",
  exotel_app_id: c?.exotel_app_id ?? "",
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

/**
 * Identifiers are sent as typed (blank clears them); secrets only when a new one was typed.
 * Only the chosen calling provider's fields are sent, so the other provider's saved details are
 * kept and switching back needs nothing re-entered.
 */
export function integrationsPayload(f: IntegrationForm) {
  const exotel = f.telephony_provider === "exotel";
  const secretKeys = [exotel ? "exotel_api_token" : "twilio_auth_token", "whatsapp_access_token", "whatsapp_app_secret"] as const;
  const secrets = Object.fromEntries(secretKeys.filter((k) => f[k].trim() !== "").map((k) => [k, f[k].trim()]));
  const calling = exotel
    ? {
        exotel_api_key: f.exotel_api_key, exotel_account_sid: f.exotel_account_sid, exotel_subdomain: f.exotel_subdomain,
        exotel_number: f.exotel_number, exotel_app_id: f.exotel_app_id,
      }
    : { twilio_account_sid: f.twilio_account_sid, twilio_from_number: f.twilio_from_number };
  return { telephony_provider: f.telephony_provider, ...calling, whatsapp_phone_number_id: f.whatsapp_phone_number_id, ...secrets };
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

function CopyLine({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="copy-row">
      <code>{value}</code>
      <button type="button" className="btn ghost sm" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
    </div>
  );
}

/** Exotel has no API for building a call flow, so the company does these steps once in its Exotel dashboard. */
function ExotelSetup({ company }: { company: Company }) {
  if (company.telephony_provider !== "exotel" || !company.exotel_stream_url || !company.exotel_passthru_url) {
    return <span className="hint">Choose Exotel and save once: the two addresses to paste into your Exotel flow will appear here.</span>;
  }
  return (
    <div className="form">
      <strong>Set up the flow in Exotel (one time)</strong>
      <ol className="steps">
        <li>Ask Exotel support to enable the <strong>Voicebot applet</strong> on your account, if it is not already.</li>
        <li>
          In Exotel, open <strong>App Bazaar</strong>, create (or edit) your flow and make its <strong>first step</strong> a{" "}
          <strong>Voicebot</strong> applet with this URL (it ends in a secret key, so treat it like a password):
          <CopyLine value={company.exotel_stream_url} />
        </li>
        <li>Tick <strong>Record</strong> on the Voicebot applet if you want call recordings.</li>
        <li>
          After it, add a <strong>Passthru</strong> applet with this URL:
          <CopyLine value={company.exotel_passthru_url} />
        </li>
        <li>End the flow with a <strong>Hangup</strong> applet and save it.</li>
        <li>Copy the flow's <strong>App ID</strong> (the number in its URL) into the App ID box above, and save here.</li>
        <li>For inbound calls, assign this flow to your ExoPhone in Exotel, then switch on Inbound calls below.</li>
      </ol>
      <span className="hint">
        Both addresses change whenever your public address (PUBLIC_BASE_URL) changes. If a call connects but the agent is
        silent, the Voicebot URL in Exotel is out of date: paste the current one from here again.
      </span>
    </div>
  );
}

export function IntegrationFields({ company, form, set }: { company: Company; form: IntegrationForm; set: Setter<IntegrationForm> }) {
  const saved = (isSet: boolean) => (isSet ? "Saved — leave blank to keep it" : "Not set");
  const exotel = form.telephony_provider === "exotel";
  // The badge describes the saved provider; a provider picked but not saved yet is not connected.
  const connected = company.calls_connected && company.telephony_provider === form.telephony_provider;
  return (
    <>
      <div className="question-card form">
        <div className="channel-head"><strong>Voice calls</strong><ChannelStatus connected={connected} /></div>
        <div className="form-row">
          <label>
            Calling provider
            <Select value={form.telephony_provider} onChange={(e) => set({ telephony_provider: e.target.value as TelephonyProvider })}>
              <option value="twilio">Twilio</option>
              <option value="exotel">Exotel</option>
            </Select>
          </label>
        </div>
        <span className="hint">
          One provider is used at a time, for both campaign calls and inbound calls. The other provider's saved details are
          kept, so you can switch back later. Pause running campaigns before switching.
        </span>
        {!exotel ? (
          <>
            <span className="hint">
              Calls are placed from this Twilio account and number, and billed by Twilio to that account. Find these in the
              Twilio Console under Account Info. Until all three are saved, campaigns cannot place calls.
            </span>
            <div className="form-row">
              <label>Account SID<input value={form.twilio_account_sid} onChange={(e) => set({ twilio_account_sid: e.target.value })} placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" autoComplete="off" /></label>
              <label>Auth token<input type="password" value={form.twilio_auth_token} onChange={(e) => set({ twilio_auth_token: e.target.value })} placeholder={saved(company.twilio_auth_token_set)} autoComplete="new-password" /></label>
              <label>Calling number<input value={form.twilio_from_number} onChange={(e) => set({ twilio_from_number: e.target.value })} placeholder="+14155550123" autoComplete="off" /></label>
            </div>
          </>
        ) : (
          <>
            <span className="hint">
              Calls are placed from this Exotel account and ExoPhone. Find the API key, token and Account SID in Exotel under
              API settings. All of these, including the App ID, are needed before campaigns can place calls.
            </span>
            <div className="form-row">
              <label>API key<input value={form.exotel_api_key} onChange={(e) => set({ exotel_api_key: e.target.value })} autoComplete="off" /></label>
              <label>API token<input type="password" value={form.exotel_api_token} onChange={(e) => set({ exotel_api_token: e.target.value })} placeholder={saved(company.exotel_api_token_set)} autoComplete="new-password" /></label>
              <label>Account SID<input value={form.exotel_account_sid} onChange={(e) => set({ exotel_account_sid: e.target.value })} placeholder="yourcompany1" autoComplete="off" /></label>
            </div>
            <div className="form-row">
              <label>
                Region
                <Select value={form.exotel_subdomain} onChange={(e) => set({ exotel_subdomain: e.target.value })}>
                  <option value="api.in.exotel.com">India (Mumbai)</option>
                  <option value="api.exotel.com">Singapore</option>
                </Select>
              </label>
              <label>ExoPhone number<input value={form.exotel_number} onChange={(e) => set({ exotel_number: e.target.value })} placeholder="08047112233" autoComplete="off" /></label>
              <label>App ID <span className="hint">(your flow)</span><input value={form.exotel_app_id} onChange={(e) => set({ exotel_app_id: e.target.value })} placeholder="e.g. 123456" autoComplete="off" /></label>
            </div>
            <ExotelSetup company={company} />
          </>
        )}
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
      <span className="hint">Tokens are stored encrypted and are never shown again. To disconnect a channel, clear its Account SID (or API key) or Phone number ID and save.</span>
    </>
  );
}

const INBOUND_LANGUAGES = [
  { code: "en-IN", label: "English", greeting: "Hello, thank you for calling {company}. I am the virtual assistant." },
  { code: "hi-IN", label: "Hindi", greeting: "नमस्ते, {company} को call करने के लिए धन्यवाद। मैं virtual assistant हूँ।" },
];
// Every language a call can be held in. English and Hindi lines are written by hand; the others are
// translated automatically the first time a call needs them.
const CALL_LANGUAGES = [
  ...INBOUND_LANGUAGES.map(({ code, label }) => ({ code, label })),
  { code: "mr-IN", label: "Marathi" }, { code: "gu-IN", label: "Gujarati" }, { code: "pa-IN", label: "Punjabi" },
  { code: "bn-IN", label: "Bengali" }, { code: "ta-IN", label: "Tamil" }, { code: "te-IN", label: "Telugu" },
  { code: "kn-IN", label: "Kannada" }, { code: "ml-IN", label: "Malayalam" }, { code: "od-IN", label: "Odia" },
];

/** Inbound support calls on the company's Twilio number: the switch, the starting language and the greeting. */
export function InboundCard({ company, onSave, saving }: {
  company: Company; onSave: (body: Record<string, unknown>) => void; saving?: boolean;
}) {
  const [enabled, setEnabled] = useState(company.inbound_enabled);
  const [language, setLanguage] = useState(company.inbound_language);
  const [greeting, setGreeting] = useState(company.inbound_greeting ?? "");
  const [languages, setLanguages] = useState(company.inbound_languages);
  useEffect(() => {
    setEnabled(company.inbound_enabled);
    setLanguage(company.inbound_language);
    setGreeting(company.inbound_greeting ?? "");
    setLanguages(company.inbound_languages);
  }, [company]);
  const toggle = (code: string) => setLanguages((l) => (l.includes(code) ? l.filter((c) => c !== code) : [...l, code]));
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
        When a customer calls {(company.telephony_provider === "exotel" ? company.exotel_number : company.twilio_from_number) ?? "your number"}, the AI agent answers. It uses the same knowledge base as
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
          <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
            {INBOUND_LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
          </Select>
        </label>
      </div>
      <div>
        <div className="field-label">The call may follow the caller into</div>
        <div className="check-grid">
          {CALL_LANGUAGES.map((l) => (
            <label className="checkbox" key={l.code}>
              <input type="checkbox" checked={l.code === language || languages.includes(l.code)} disabled={l.code === language} onChange={() => toggle(l.code)} />
              {l.label}
            </label>
          ))}
        </div>
      </div>
      <span className="hint">
        The call continues in the caller's language, but only one ticked here. One odd sentence never flips the call: a
        Hindi caller using English words stays in Hindi, and the call changes language only when the caller really
        switches or asks ("please speak in English"). Tick only the languages your customers use; fewer choices means fewer mistakes.
      </span>
      <label>
        Greeting <span className="hint">— leave blank to use the standard greeting</span>
        <textarea value={greeting} onChange={(e) => setGreeting(e.target.value)} placeholder={fallback.greeting.replace("{company}", company.name)} />
      </label>
      <div>
        <button className="btn" disabled={saving} onClick={() => onSave({ inbound_enabled: blocked ? false : enabled, inbound_language: language, inbound_greeting: greeting, inbound_languages: languages })}>
          Save inbound settings
        </button>
      </div>
      <span className="hint">
        {company.telephony_provider === "exotel"
          ? "On Exotel, also assign your flow to the ExoPhone in the Exotel dashboard: Exotel has no way for us to do that for you."
          : "Switching this on sets the \"a call comes in\" address on your Twilio number automatically; switching it off removes it. Nothing to copy into Twilio."}
      </span>
    </div>
  );
}

/** A generated login. The password exists only in this response, so it must be copied now. */
/** Email support through the company's own SendGrid account: the switch, the address, the key and the set-up steps. */
export function EmailCard({ company, onSave, saving }: {
  company: Company; onSave: (body: Record<string, unknown>) => void; saving?: boolean;
}) {
  const [enabled, setEnabled] = useState(company.email_enabled);
  const [address, setAddress] = useState(company.email_address ?? "");
  const [fromName, setFromName] = useState(company.email_from_name ?? "");
  const [key, setKey] = useState("");
  useEffect(() => {
    setEnabled(company.email_enabled);
    setAddress(company.email_address ?? "");
    setFromName(company.email_from_name ?? "");
    setKey("");
  }, [company]);
  const domain = address.includes("@") ? address.split("@")[1] : "yourcompany.com";
  const save = () => {
    const body: Record<string, unknown> = { email_address: address.trim() || null, email_from_name: fromName.trim() || null };
    if (key.trim()) body.sendgrid_api_key = key.trim();
    // Switching on needs the address and key saved; the server says so if they are missing.
    body.email_enabled = enabled;
    onSave(body);
  };

  return (
    <div className="card form">
      <div className="channel-head">
        <h2 style={{ margin: 0 }}>Email support <span className="hint">(SendGrid)</span></h2>
        <div className="row">
          <ChannelStatus connected={company.email_connected} />
          <Badge value={company.email_active ? "on" : "off"} />
        </div>
      </div>
      <span className="hint">
        Customers email your support address; the AI assistant answers from the same knowledge base as WhatsApp and
        calls, raises a ticket when it cannot help, and your team can reply from the Conversations page. Emails are sent
        through your own SendGrid account.
      </span>
      {/* Switched on by the company, yet nothing is sent (e.g. email is off for the whole platform): say why. */}
      {company.email_enabled && company.email_problem && <div className="error" role="status">{company.email_problem}</div>}
      <label className="checkbox">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Send emails (AI replies and your team's replies)
      </label>
      <span className="hint">While this is off, incoming emails are still saved, but nothing is sent to the customer.</span>
      <div className="form-row">
        <label>Support email address<input type="email" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="support@yourcompany.com" autoComplete="off" /></label>
        <label>Sender name<input value={fromName} onChange={(e) => setFromName(e.target.value)} placeholder={`${company.name} Support`} autoComplete="off" /></label>
        <label>SendGrid API key<input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={company.sendgrid_api_key_set ? "Saved — leave blank to keep it" : "Not set"} autoComplete="new-password" /></label>
      </div>
      <div><button className="btn" disabled={saving} onClick={save}>Save email settings</button></div>
      <details>
        <summary>How to set up SendGrid</summary>
        <ol className="steps" style={{ marginTop: 12 }}>
          <li>In SendGrid, open <strong>Settings → Sender Authentication</strong> and verify your domain <code>{domain}</code>, so emails from your support address are delivered.</li>
          <li>Open <strong>Settings → API Keys</strong>, create a key with the <strong>Mail Send</strong> permission, paste it above and save.</li>
          <li>
            Open <strong>Settings → Inbound Parse → Add Host &amp; URL</strong>. Receiving domain: <code>{domain}</code> (or a
            sub-domain such as <code>support.{domain}</code>). Destination URL:
            {company.email_webhook_url ? <CopyLine value={company.email_webhook_url} /> : <div className="hint">Save your support email address first; the address to paste appears here.</div>}
          </li>
          <li>At your domain provider, add the <strong>MX record</strong> SendGrid shows for that receiving domain (<code>mx.sendgrid.net</code>, priority 10).</li>
          <li>Tick "Send emails" above and save. Send a test email to your support address; it appears under Conversations.</li>
        </ol>
        <p className="hint">The destination URL is private to your company. It changes if the platform's public address changes.</p>
      </details>
    </div>
  );
}

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
