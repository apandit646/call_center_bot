import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { PlatformBilling } from "../../api/types";
import { money } from "../../components/Billing";
import { Badge, Empty, ErrorBox, Loading, PageHeader, Stat } from "../../components/ui";

/** The platform admin's billing overview: every company's price, usage this month and what it owes. */
export default function PlatformBillingPage() {
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["platform-billing"],
    queryFn: async () => (await api.get<PlatformBilling>("/platform/billing")).data,
    refetchInterval: 60_000,
  });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const currency = data.companies.find((c) => c.currency)?.currency ?? "INR";

  return (
    <>
      <PageHeader title="Billing" subtitle={`Invoices are created on day ${data.billing_day} of every month. Open a company to set its prices or manage its invoices.`} />
      {!data.billing_enabled && <div className="error" role="status">Billing is switched off for the whole platform (BILLING_ENABLED is false in the server settings). Nothing is priced or invoiced.</div>}
      {data.billing_enabled && !data.stripe_connected && (
        <div className="error" role="status">Stripe is not connected: invoices are created but cannot be paid online. Set STRIPE_SECRET_KEY in the server's .env file and restart.</div>
      )}
      {data.stripe_connected && !data.stripe_webhook_ready && (
        <div className="error" role="status">
          <span>
            Stripe cannot tell this system when an invoice is paid yet. In Stripe, add a webhook endpoint for <code>{data.stripe_webhook_url}</code> (events
            invoice.paid, invoice.payment_failed, invoice.voided), put its signing secret in STRIPE_WEBHOOK_SECRET and restart. Until then, mark invoices paid by hand.
          </span>
        </div>
      )}
      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat variant="feature" label="Waiting to be paid" value={money(data.totals.due, currency)} hint="open invoices, all companies" />
        <Stat label="Paid so far" value={money(data.totals.paid, currency)} hint="all paid invoices" />
        <Stat label="This month so far" value={money(data.totals.this_period, currency)} hint="estimate of the next invoices" />
        <Stat label="Companies billed" value={data.companies.filter((c) => c.billing_on).length} hint={`of ${data.companies.length}`} />
      </div>
      {!data.companies.length ? <Empty>No companies yet.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Company</th><th>Price per minute</th><th>Calls this month</th><th>Average cost per call</th><th>This month so far</th><th>Owes</th><th>Status</th></tr>
            </thead>
            <tbody>
              {data.companies.map((c) => (
                <tr key={c.company_id} className="clickable" onClick={() => navigate(`/companies/${c.company_id}?tab=billing`)}>
                  <td><strong>{c.name}</strong></td>
                  <td className="small">
                    {c.currency ? <>{money(c.outbound_per_minute, c.currency)} campaign<br />{money(c.inbound_per_minute, c.currency)} inbound</> : <span className="muted">no prices set</span>}
                  </td>
                  <td>{c.calls.toLocaleString()}<div className="small muted">{c.call_minutes.toLocaleString()} min</div></td>
                  <td>{money(c.average_cost_per_call, c.currency ?? currency)}</td>
                  <td>{money(c.estimated_total, c.currency ?? currency)}</td>
                  <td>{c.amount_due > 0 ? <strong>{money(c.amount_due, c.currency ?? currency)}</strong> : "—"}</td>
                  <td>
                    {c.on_hold ? <Badge value="on hold" tone="red" /> : c.billing_on ? <Badge value="billed" tone="green" /> : <Badge value="not billed" tone="grey" />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
