import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { BillingSummary } from "../api/types";
import { InvoiceTable, PeriodStats, PriceList } from "../components/Billing";
import { ErrorBox, Loading, PageHeader } from "../components/ui";

/** The company admin's Billing page: its prices, this month so far, and its invoices with the link to pay. */
export default function Billing() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["billing"],
    queryFn: async () => (await api.get<BillingSummary>("/billing")).data,
    refetchInterval: 60_000,
  });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;

  return (
    <>
      <PageHeader title="Billing" subtitle="What you pay for the platform, what you have used this month, and your invoices." />
      {!data.billing_on || !data.plan ? (
        <div className="card">
          <h2>No billing yet</h2>
          <p className="muted" style={{ margin: 0 }}>Your account is not being charged at the moment. When the platform administrator sets your prices, they appear here together with your usage and invoices.</p>
        </div>
      ) : (
        <>
          {data.on_hold && (
            <div className="error" role="alert">
              An invoice is overdue, so your campaigns and callbacks are on hold. Pay it below and they start again
              automatically. Inbound calls, WhatsApp and email are not affected.
            </div>
          )}
          <PeriodStats data={data} />
          <div className="grid grid-2">
            <div className="card">
              <h2>Your prices</h2>
              <PriceList plan={data.plan} />
            </div>
            <div className="card">
              <h2>How billing works</h2>
              <ul className="steps" style={{ paddingLeft: 18 }}>
                <li>An invoice is created on the <strong>28th of every month</strong> for the month up to the 27th.</li>
                <li>You get it by email with a <strong>Pay</strong> link; it is also listed below. You have {data.due_days} days to pay.</li>
                <li>If it is still unpaid {data.grace_days} days after the due date, campaigns and callbacks pause until it is paid.</li>
              </ul>
            </div>
          </div>
        </>
      )}
      <h2 style={{ margin: "8px 4px 12px" }}>Invoices</h2>
      <InvoiceTable invoices={data.invoices.filter((i) => i.status !== "void")} />
    </>
  );
}
