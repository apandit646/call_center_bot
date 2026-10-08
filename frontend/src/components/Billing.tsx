// Billing screens shared by the company's own Billing page and the platform admin's company page.
import { useEffect, useState } from "react";
import type { BillingPlan, BillingSummary, Invoice } from "../api/types";
import { Badge, Empty, Stat, fmtDate } from "./ui";
import { useConfirm } from "./Confirm";

export const money = (amount: number | null | undefined, currency = "INR") =>
  amount == null ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency, minimumFractionDigits: 2 }).format(amount);
const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

export const EMPTY_PLAN: BillingPlan = {
  enabled: true, currency: "INR", monthly_fee: 0, outbound_per_minute: 0, inbound_per_minute: 0, whatsapp_per_message: 0,
  email_per_message: 0, tax_percent: 18, tax_label: "GST", billing_email: null, tax_id: null,
};

/** The prices a company pays, as a short list. */
export function PriceList({ plan }: { plan: BillingPlan }) {
  const c = plan.currency;
  return (
    <dl className="meta-list">
      <dt>Campaign calls</dt><dd>{money(plan.outbound_per_minute, c)} per minute</dd>
      <dt>Inbound calls and callbacks</dt><dd>{money(plan.inbound_per_minute, c)} per minute</dd>
      {plan.whatsapp_per_message > 0 && <><dt>WhatsApp replies by the assistant</dt><dd>{money(plan.whatsapp_per_message, c)} per message</dd></>}
      {plan.email_per_message > 0 && <><dt>Email replies by the assistant</dt><dd>{money(plan.email_per_message, c)} per email</dd></>}
      <dt>Monthly platform fee</dt><dd>{plan.monthly_fee > 0 ? money(plan.monthly_fee, c) : "None"}</dd>
      <dt>{plan.tax_label}</dt><dd>{plan.tax_percent}% added to every invoice</dd>
    </dl>
  );
}

/** This billing month so far: the estimate, calls, and the average cost per call. */
export function PeriodStats({ data }: { data: BillingSummary }) {
  const p = data.this_period;
  const c = data.plan?.currency ?? "INR";
  return (
    <>
      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat variant="feature" label="This month so far" value={money(p.estimated_total, c)}
          hint={`${day(data.period.from)} to today, with fee and ${data.plan?.tax_label ?? "tax"}`} />
        <Stat label="Average cost per call" value={money(p.average_cost_per_call, c)}
          hint={p.calls ? `${p.average_minutes_per_call} min per call on average` : "no charged calls yet"} />
        <Stat label="Calls charged" value={p.calls.toLocaleString()} hint={`${p.call_minutes.toLocaleString()} minutes · ${money(p.call_amount, c)}`} />
        <Stat variant={data.amount_due > 0 ? "alert" : undefined} label="To pay now" value={money(data.amount_due, c)}
          hint={data.amount_due > 0 ? "see the invoices below" : `next invoice on ${day(data.next_invoice_date)}`} />
      </div>
      <div className="card">
        <div className="section-title">
          <div>
            <h2>Used this month</h2>
            <div className="muted small">Calls are charged per started minute. Calls that were not answered are free.</div>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>What</th><th>Count</th><th>Charged</th><th>Amount</th></tr></thead>
            <tbody>
              {p.usage.map((u) => (
                <tr key={u.kind}>
                  <td>{u.label}</td>
                  <td>{u.count.toLocaleString()}</td>
                  <td>{u.kind.endsWith("_call") ? `${u.quantity.toLocaleString()} min` : `${u.quantity.toLocaleString()} messages`}</td>
                  <td>{money(u.amount, c)}</td>
                </tr>
              ))}
              {p.monthly_fee > 0 && <tr><td>Monthly platform fee</td><td /><td /><td>{money(p.monthly_fee, c)}</td></tr>}
              <tr><td>{data.plan?.tax_label ?? "Tax"} {data.plan?.tax_percent ?? 0}%</td><td /><td /><td>{money(p.tax_amount, c)}</td></tr>
              <tr><td><strong>Estimated invoice</strong></td><td /><td /><td><strong>{money(p.estimated_total, c)}</strong></td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function statusOf(i: Invoice): { value: string; tone?: "red" | "amber" | "green" | "grey" } {
  if (i.status === "paid") return { value: "paid", tone: "green" };
  if (i.status === "void") return { value: "cancelled", tone: "grey" };
  return i.overdue_days > 0 ? { value: `overdue ${i.overdue_days}d`, tone: "red" } : { value: "due", tone: "amber" };
}

/** Invoices, newest first. Click one to see its lines. `actions` adds the platform admin's buttons. */
export function InvoiceTable({ invoices, actions, busy }: {
  invoices: Invoice[]; actions?: (invoice: Invoice, action: "mark-paid" | "void" | "send") => void; busy?: boolean;
}) {
  const ask = useConfirm();
  const [open, setOpen] = useState<string | null>(null);
  if (!invoices.length) return <Empty>No invoices yet. The first one is created on the next billing day.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Invoice</th><th>Period</th><th>Total</th><th>Status</th><th>Due</th><th /></tr></thead>
        <tbody>
          {invoices.map((i) => {
            const st = statusOf(i);
            return [
              <tr key={i.id} className="clickable" onClick={() => setOpen(open === i.id ? null : i.id)}>
                <td><strong>{i.label}</strong>{i.kind === "manual" && <div className="small muted">extra invoice</div>}</td>
                <td className="small">{day(i.period_start)} – {day(i.period_end)}</td>
                <td><strong>{money(i.total, i.currency)}</strong></td>
                <td><Badge value={st.value} tone={st.tone} />{i.paid_via === "offline" && <div className="small muted">paid by transfer</div>}</td>
                <td className="small nowrap">{i.status === "paid" ? `paid ${fmtDate(i.paid_at)}` : day(i.due_date)}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <div className="cell-actions">
                    {i.status === "open" && i.hosted_url && <a className="btn sm" href={i.hosted_url} target="_blank" rel="noreferrer">Pay now</a>}
                    {i.pdf_url && <a className="btn ghost sm" href={i.pdf_url} target="_blank" rel="noreferrer">PDF</a>}
                    {actions && i.status === "open" && (
                      <>
                        {!i.hosted_url && <button className="btn ghost sm" disabled={busy} onClick={() => actions(i, "send")}>Send via Stripe</button>}
                        <button className="btn ghost sm" disabled={busy}
                          onClick={async () => (await ask({ title: `Mark ${i.label} as paid?`, message: "Use this when the company paid outside Stripe, for example by bank transfer or cash. Its Stripe copy is closed.", confirm: "Mark as paid" })) && actions(i, "mark-paid")}>Mark paid</button>
                        <button className="btn danger sm" disabled={busy}
                          onClick={async () => (await ask({ title: `Cancel ${i.label}?`, message: "The company will not have to pay it. What it billed moves onto the next invoice.", confirm: "Cancel invoice", cancel: "Keep it", danger: true })) && actions(i, "void")}>Cancel</button>
                      </>
                    )}
                  </div>
                </td>
              </tr>,
              open === i.id && (
                <tr key={`${i.id}-lines`}>
                  <td colSpan={6} style={{ background: "var(--panel-2)", borderRadius: 16 }}>
                    <table>
                      <tbody>
                        {i.lines.map((l, n) => <tr key={n}><td className="cell-wrap" style={{ maxWidth: 620 }}>{l.description}</td><td className="nowrap">{money(l.amount, i.currency)}</td></tr>)}
                        <tr><td>Subtotal</td><td className="nowrap">{money(i.subtotal, i.currency)}</td></tr>
                        <tr><td>{i.tax_label} {i.tax_percent}%</td><td className="nowrap">{money(i.tax_amount, i.currency)}</td></tr>
                        <tr><td><strong>Total</strong></td><td className="nowrap"><strong>{money(i.total, i.currency)}</strong></td></tr>
                      </tbody>
                    </table>
                    {i.note && <p className="small muted" style={{ margin: "8px 12px" }}>{i.note}</p>}
                    {i.stripe_error && <div className="error" style={{ margin: "8px 0 0" }}>Not on Stripe yet: {i.stripe_error}</div>}
                    {i.status === "open" && !i.hosted_url && !i.stripe_error && <p className="small muted" style={{ margin: "8px 12px" }}>Online payment is not available for this invoice yet.</p>}
                  </td>
                </tr>
              ),
            ];
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The platform admin's price editor for one company. */
export function PlanEditor({ plan, saving, onSave }: { plan: BillingPlan | null; saving?: boolean; onSave: (plan: BillingPlan) => void }) {
  const [form, setForm] = useState<BillingPlan>(plan ?? EMPTY_PLAN);
  useEffect(() => setForm(plan ?? EMPTY_PLAN), [plan]);
  const set = (patch: Partial<BillingPlan>) => setForm((f) => ({ ...f, ...patch }));
  const num = (key: keyof BillingPlan, label: string, hint?: string, step = "0.01") => (
    <label>
      {label} {hint && <span className="hint">{hint}</span>}
      <input type="number" min={0} step={step} value={form[key] as number} onChange={(e) => set({ [key]: e.target.value === "" ? 0 : Number(e.target.value) } as Partial<BillingPlan>)} />
    </label>
  );
  return (
    <form className="card form" onSubmit={(e) => { e.preventDefault(); onSave(form); }}>
      <div className="channel-head">
        <h2 style={{ margin: 0 }}>Prices for this company</h2>
        <Badge value={plan ? (plan.enabled ? "billing on" : "billing off") : "not set"} tone={plan?.enabled ? "green" : "grey"} />
      </div>
      <span className="hint">
        New prices apply to calls from now on; calls already made keep the price they were recorded at. Until prices are
        saved with billing on, this company is not charged and gets no invoices.
      </span>
      <label className="checkbox">
        <input type="checkbox" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        Bill this company
      </label>
      <div className="form-row">
        {num("outbound_per_minute", "Campaign call", "per minute")}
        {num("inbound_per_minute", "Inbound call / callback", "per minute")}
        {num("monthly_fee", "Monthly platform fee", "0 = none", "1")}
      </div>
      <div className="form-row">
        {num("whatsapp_per_message", "WhatsApp reply by the assistant", "per message, 0 = free")}
        {num("email_per_message", "Email reply by the assistant", "per email, 0 = free")}
        {num("tax_percent", "Tax %", "added on top")}
      </div>
      <div className="form-row">
        <label>Currency<input value={form.currency} maxLength={3} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} /></label>
        <label>Tax name<input value={form.tax_label} onChange={(e) => set({ tax_label: e.target.value })} placeholder="GST" /></label>
        <label>Send invoices to <span className="hint">blank = the company's email</span>
          <input type="email" value={form.billing_email ?? ""} onChange={(e) => set({ billing_email: e.target.value || null })} placeholder="accounts@company.com" />
        </label>
        <label>Company tax number <span className="hint">GSTIN</span>
          <input value={form.tax_id ?? ""} onChange={(e) => set({ tax_id: e.target.value || null })} />
        </label>
      </div>
      <div><button className="btn" disabled={saving || form.currency.length !== 3}>{plan ? "Save prices" : "Start billing this company"}</button></div>
    </form>
  );
}
