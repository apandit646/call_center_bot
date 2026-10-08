import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { PlatformDashboard } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { money } from "../../components/Billing";
import { shortDay } from "../../components/Charts";
import { IconAlert, IconBuilding, IconCard, IconCheck, IconClock, IconPhone, IconTicket } from "../../components/Icons";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Stat, type Tone } from "../../components/ui";

type Item = PlatformDashboard["attention"][number];
const LEVEL: Record<Item["level"], { label: string; tone: Tone }> = {
  high: { label: "act now", tone: "red" }, medium: { label: "soon", tone: "amber" }, low: { label: "to know", tone: "grey" },
};
const link = (a: Item) => (a.target === "issues" ? "/issues" : `/companies/${a.company_id}${a.target === "billing" ? "?tab=billing" : ""}`);
const OPEN: Record<Item["target"], string> = { issues: "Open reports", billing: "Open billing", company: "Open company" };

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

/** The platform admin's home page: today across every company, and what needs them. */
export default function PlatformDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["platform-dashboard"],
    queryFn: async () => (await api.get<PlatformDashboard>("/platform/dashboard")).data,
    refetchInterval: 30_000,
  });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const t = data.totals, s = data.setup;
  const busy = data.companies.filter((c) => c.inbound_calls + c.campaign_calls + c.tickets + c.conversations + c.live_calls > 0);
  const setup: { name: string; ok: boolean; off: string }[] = [
    { name: "Billing", ok: s.billing_enabled, off: "Switched off (BILLING_ENABLED is false in the server settings): nothing is priced or invoiced." },
    { name: "Stripe account", ok: s.stripe_connected, off: "Not connected: invoices are created but cannot be paid online. Set STRIPE_SECRET_KEY in the server's .env file." },
    { name: "Stripe payment messages", ok: s.stripe_webhook_ready, off: "Not set up: Stripe cannot tell this system when an invoice is paid. See the Billing page for the steps." },
    { name: "Email channel", ok: s.email_enabled, off: "Switched off (EMAIL_ENABLED is false in the server settings): no company can send email replies." },
  ];

  return (
    <>
      <PageHeader
        title={`${greeting()}, ${user?.full_name.split(" ")[0] ?? ""}`}
        subtitle={`Today, ${shortDay(data.date)}, across every company (days are counted in ${data.timezone} time). Updates every 30 seconds.`}
        actions={<Link className="btn ghost" to="/reports">Open reports</Link>}
      />

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat variant="feature" label="Calls today" value={t.calls_today.toLocaleString()} icon={<IconPhone size={20} />}
          hint={`${t.inbound_calls_today.toLocaleString()} inbound · ${t.campaign_calls_today.toLocaleString()} campaign`} />
        <Stat label="On the line now" value={t.live_calls} icon={<IconClock size={20} />} tone={t.live_calls ? "green" : "grey"}
          hint={`${t.running_campaigns} ${t.running_campaigns === 1 ? "campaign" : "campaigns"} running`} />
        <Stat label="Active companies" value={t.active_companies} icon={<IconBuilding size={20} />} tone="violet"
          hint={`of ${t.companies} · ${t.users.toLocaleString()} users`} />
        <Stat label="Tickets today" value={t.tickets_today.toLocaleString()} icon={<IconTicket size={20} />} tone="grey" hint={`${t.conversations_today.toLocaleString()} conversations`} />
        <Stat label="Used this billing month" value={money(t.period_usage, data.currency)} icon={<IconCard size={20} />} tone="grey"
          hint={`since ${shortDay(t.period_from)}, before monthly fees and tax`} />
        <Stat variant={t.amount_overdue > 0 ? "alert" : undefined} label="Waiting to be paid" value={money(t.amount_due, data.currency)} icon={<IconAlert size={20} />} tone="grey"
          hint={t.amount_overdue > 0 ? `${money(t.amount_overdue, data.currency)} of it is overdue` : `${t.open_invoices} open ${t.open_invoices === 1 ? "invoice" : "invoices"}, none overdue`} />
        <Stat label="Open issue reports" value={t.open_issues} tone={t.open_issues ? "amber" : "grey"} icon={<IconAlert size={20} />}
          hint={t.open_issues ? "from company admins, waiting for you" : "nothing waiting"} />
      </div>
      {data.other_currencies.length > 0 && (
        <p className="muted small">Money is shown in {data.currency}. Companies billed in {data.other_currencies.join(", ")} are not added in; see Billing for those.</p>
      )}

      <div className="card">
        <div className="section-title">
          <h2>Needs your attention</h2>
          {data.attention.length > 0 && <span className="muted small">{data.attention.length} {data.attention.length === 1 ? "item" : "items"}, most serious first</span>}
        </div>
        {!data.attention.length ? <p className="muted" style={{ margin: 0 }}>Nothing needs you right now.</p> : (
          <ul className="attention">
            {data.attention.map((a) => (
              <li key={`${a.company_id}-${a.kind}`}>
                <Badge value={LEVEL[a.level].label} tone={LEVEL[a.level].tone} />
                <span className="attention-text"><strong>{a.company}</strong>{a.text}</span>
                <Link className="btn ghost sm" to={link(a)}>{OPEN[a.target]}</Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <div className="section-title">
          <h2>Today by company</h2>
          <Link to="/companies" className="btn ghost sm">All companies →</Link>
        </div>
        {!busy.length ? <Empty icon={<IconBuilding size={28} />}>No company has had a call, ticket or conversation yet today.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Company</th><th className="num">Inbound calls</th><th className="num">Campaign calls</th><th className="num">On the line</th><th className="num">Tickets</th><th className="num">Conversations</th></tr>
              </thead>
              <tbody>
                {busy.map((c) => (
                  <tr key={c.company_id} className="clickable" onClick={() => navigate(`/companies/${c.company_id}`)}>
                    <td><strong>{c.name}</strong>{!c.is_active && <> <Badge value="suspended" /></>}</td>
                    <td className="num">{c.inbound_calls.toLocaleString()}</td>
                    <td className="num">{c.campaign_calls.toLocaleString()}</td>
                    <td className="num">{c.live_calls || "—"}</td>
                    <td className="num">{c.tickets.toLocaleString()}</td>
                    <td className="num">{c.conversations.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {busy.length > 0 && busy.length < data.companies.length && (
          <p className="muted small" style={{ marginBottom: 0 }}>{data.companies.length - busy.length} more with nothing yet today.</p>
        )}
      </div>

      <div className="card">
        <h2>Platform setup</h2>
        <dl className="meta-list setup-list">
          {setup.map((row) => (
            <div key={row.name} style={{ display: "contents" }}>
              <dt>{row.name}</dt>
              <dd>{row.ok ? <><IconCheck size={16} aria-hidden /> Ready</> : <span style={{ fontWeight: 500 }}><Badge value="off" tone="amber" /> {row.off}</span>}</dd>
            </div>
          ))}
          <dt>Invoices</dt><dd>Created on day {s.billing_day} of every month</dd>
        </dl>
      </div>
    </>
  );
}
