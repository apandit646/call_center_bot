import { Linking, StyleSheet, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { BillingSummary, Invoice } from "@/api/types";
import { Badge, Button, Card, Empty, ErrorBox, H2, KV, Loading, Notice, Row, Screen, Stat, T, fmtDate, money, type Tone } from "@/components/ui";

const day = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function statusOf(i: Invoice): { value: string; tone: Tone } {
  if (i.status === "paid") return { value: "paid", tone: "green" };
  if (i.status === "void") return { value: "cancelled", tone: "grey" };
  return i.overdue_days > 0 ? { value: `overdue ${i.overdue_days}d`, tone: "red" } : { value: "due", tone: "amber" };
}

/** The company admin's billing: prices, this month so far, and invoices with the link to pay. */
export default function Billing() {
  const { data, isLoading, error, refetch, isRefetching } = useQuery({
    queryKey: ["billing"],
    queryFn: async () => (await api.get<BillingSummary>("/billing")).data,
    refetchInterval: 60_000,
  });
  if (isLoading) return <Loading />;
  if (error || !data) return <Screen><ErrorBox error={error ?? new Error("Billing is not available")} /></Screen>;
  const p = data.this_period;
  const plan = data.plan;
  const c = plan?.currency ?? "INR";
  const invoices = data.invoices.filter((i) => i.status !== "void");

  return (
    <Screen refreshing={isRefetching} onRefresh={refetch}>
      {!data.billing_on || !plan ? (
        <Card>
          <H2>No billing yet</H2>
          <T muted>Your account is not being charged at the moment. When the platform administrator sets your prices, they appear here with your usage and invoices.</T>
        </Card>
      ) : (
        <>
          {data.on_hold && <Notice tone="red">An invoice is overdue, so your campaigns and callbacks are on hold. Pay it below and they start again automatically.</Notice>}
          <View style={s.grid}>
            <Stat feature label="This month so far" value={money(p.estimated_total, c)} hint={`from ${day(data.period.from)}, with fee and ${plan.tax_label}`} />
            <Stat label="To pay now" value={money(data.amount_due, c)} tone={data.amount_due > 0 ? "red" : "green"}
              hint={data.amount_due > 0 ? "see the invoices below" : `next invoice ${day(data.next_invoice_date)}`} />
            <Stat label="Average cost per call" value={money(p.average_cost_per_call, c)} tone="violet"
              hint={p.calls ? `${p.average_minutes_per_call} min per call` : "no charged calls yet"} />
            <Stat label="Calls charged" value={p.calls} hint={`${p.call_minutes} min · ${money(p.call_amount, c)}`} />
          </View>

          <Card>
            <H2>Used this month</H2>
            {p.usage.map((u) => (
              <KV key={u.kind} k={u.label}>{`${u.count} · ${u.quantity} ${u.kind.endsWith("_call") ? "min" : "msgs"} · ${money(u.amount, c)}`}</KV>
            ))}
            {p.monthly_fee > 0 && <KV k="Monthly fee">{money(p.monthly_fee, c)}</KV>}
            <KV k={`${plan.tax_label} ${plan.tax_percent}%`}>{money(p.tax_amount, c)}</KV>
            <KV k="Estimated invoice"><T bold>{money(p.estimated_total, c)}</T></KV>
          </Card>

          <Card>
            <H2>Your prices</H2>
            <KV k="Campaign calls">{`${money(plan.outbound_per_minute, c)} per minute`}</KV>
            <KV k="Inbound calls">{`${money(plan.inbound_per_minute, c)} per minute`}</KV>
            {plan.whatsapp_per_message > 0 && <KV k="WhatsApp replies">{`${money(plan.whatsapp_per_message, c)} per message`}</KV>}
            {plan.email_per_message > 0 && <KV k="Email replies">{`${money(plan.email_per_message, c)} per email`}</KV>}
            <KV k="Monthly fee">{plan.monthly_fee > 0 ? money(plan.monthly_fee, c) : "None"}</KV>
            <T small muted>Calls are charged per started minute. Invoices are created on the 28th; you have {data.due_days} days to pay.</T>
          </Card>
        </>
      )}

      <H2>Invoices</H2>
      {!invoices.length && <Empty>No invoices yet.</Empty>}
      {invoices.map((i) => {
        const st = statusOf(i);
        return (
          <Card key={i.id}>
            <Row spread>
              <T bold>{i.label}</T>
              <Badge value={st.value} tone={st.tone} />
            </Row>
            <T small muted>{day(i.period_start)} – {day(i.period_end)}</T>
            <T bold style={s.total}>{money(i.total, i.currency)}</T>
            <T small muted>{i.status === "paid" ? `Paid ${fmtDate(i.paid_at)}` : `Due ${day(i.due_date)}`}</T>
            {i.lines.map((l, n) => <T key={n} small muted>{l.description}: {money(l.amount, i.currency)}</T>)}
            <Row>
              {i.status === "open" && i.hosted_url && <Button small title="Pay now" onPress={() => Linking.openURL(i.hosted_url!)} />}
              {i.pdf_url && <Button small variant="ghost" title="PDF" onPress={() => Linking.openURL(i.pdf_url!)} />}
            </Row>
            {i.status === "open" && !i.hosted_url && <T small muted>Online payment is not available for this invoice yet.</T>}
          </Card>
        );
      })}
    </Screen>
  );
}

const s = StyleSheet.create({
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  total: { fontSize: 22 },
});
