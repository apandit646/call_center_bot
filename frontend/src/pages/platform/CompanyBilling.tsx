import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { BillingPlan, BillingSummary, Invoice } from "../../api/types";
import { InvoiceTable, PeriodStats, PlanEditor } from "../../components/Billing";
import { ErrorBox, Loading } from "../../components/ui";

/** The Billing tab of a company, for the platform admin: its prices, usage, and invoice controls. */
export default function CompanyBilling({ companyId }: { companyId: string }) {
  const qc = useQueryClient();
  const key = ["company-billing", companyId];
  const { data, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: async () => (await api.get<BillingSummary>(`/companies/${companyId}/billing`)).data,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["platform-billing"] });
  };
  const savePlan = useMutation({
    mutationFn: async (plan: BillingPlan) => (await api.put<BillingSummary>(`/companies/${companyId}/billing-plan`, plan)).data,
    onSuccess: (d) => { qc.setQueryData(key, d); qc.invalidateQueries({ queryKey: ["platform-billing"] }); },
  });
  const billNow = useMutation({
    mutationFn: async (includeFee: boolean) => api.post(`/companies/${companyId}/invoices`, { include_fee: includeFee }),
    onSuccess: refresh,
  });
  const act = useMutation({
    mutationFn: async ({ invoice, action }: { invoice: Invoice; action: string }) => api.post(`/invoices/${invoice.id}/${action}`),
    onSuccess: refresh,
  });

  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;

  return (
    <>
      <ErrorBox error={savePlan.error || billNow.error || act.error} />
      {savePlan.isSuccess && <div className="success">Prices saved.</div>}
      {data.on_hold && <div className="error" role="status">This company's campaigns and callbacks are on hold for an overdue invoice. Marking it paid (or cancelling it) lifts the hold.</div>}
      <PlanEditor plan={data.plan} saving={savePlan.isPending} onSave={(plan) => savePlan.mutate(plan)} />
      {data.billing_on && data.plan && (
        <>
          <PeriodStats data={data} />
          <div className="card">
            <div className="section-title">
              <div>
                <h2>Bill now</h2>
                <div className="muted small">Create an invoice for the usage so far, without waiting for the billing day. The monthly invoice then covers only what comes after.</div>
              </div>
              <div className="row">
                <button className="btn ghost" disabled={billNow.isPending} onClick={() => confirm("Create an invoice for the usage so far?") && billNow.mutate(false)}>Usage only</button>
                <button className="btn ghost" disabled={billNow.isPending} onClick={() => confirm("Create an invoice for the usage so far plus the monthly fee?") && billNow.mutate(true)}>Usage + monthly fee</button>
              </div>
            </div>
          </div>
        </>
      )}
      <h2 style={{ margin: "8px 4px 12px" }}>Invoices</h2>
      <InvoiceTable invoices={data.invoices} busy={act.isPending} actions={(invoice, action) => act.mutate({ invoice, action })} />
    </>
  );
}
