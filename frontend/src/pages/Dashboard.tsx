import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { DashboardSummary } from "../api/types";
import { ErrorBox, Loading, PageHeader, Stat } from "../components/ui";

export default function Dashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: async () => (await api.get<DashboardSummary>("/dashboard/summary")).data,
    refetchInterval: 15_000,
  });

  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;

  return (
    <>
      <PageHeader title="Dashboard" subtitle="Live overview of both workflows" />

      <div className="card">
        <div className="row spread"><h2>Workflow 1 · WhatsApp Support</h2><Link to="/tickets">View tickets →</Link></div>
        <div className="grid grid-4">
          <Stat label="Conversations today" value={data.whatsapp.conversations_today} />
          <Stat label="Open conversations" value={data.whatsapp.open_conversations} />
          <Stat label="Resolved by AI today" value={data.whatsapp.resolved_by_ai_today} />
          <Stat label="Escalated today" value={data.whatsapp.escalated_today} />
          <Stat label="Open tickets" value={data.tickets.open} />
          <Stat label="Unassigned tickets" value={data.tickets.unassigned} />
          <Stat label="Tickets created today" value={data.tickets.created_today} />
          <Stat
            label="Open high / urgent"
            value={(data.tickets.open_by_priority.high ?? 0) + (data.tickets.open_by_priority.urgent ?? 0)}
          />
        </div>
      </div>

      <div className="card">
        <div className="row spread"><h2>Workflow 2 · Outbound AI Q&amp;A</h2><Link to="/campaigns">View campaigns →</Link></div>
        <div className="grid grid-4">
          <Stat label="Running campaigns" value={data.outbound.running_campaigns} />
          <Stat label="Active calls" value={data.outbound.active_calls} />
          <Stat label="Calls today" value={data.outbound.calls_today} />
          <Stat label="Completed calls today" value={data.outbound.completed_calls_today} />
        </div>
      </div>
    </>
  );
}
