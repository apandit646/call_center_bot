import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { DashboardSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useCompany } from "../hooks";
import { IconAlert, IconBot, IconChat, IconCheck, IconClock, IconPhone, IconSparkle, IconTicket, IconUser } from "../components/Icons";
import { ErrorBox, Loading, PageHeader, Stat, UsageMeter } from "../components/ui";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export default function Dashboard() {
  const { user } = useAuth();
  const { data, isLoading, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: async () => (await api.get<DashboardSummary>("/dashboard/summary")).data,
    refetchInterval: 15_000,
  });

  const { data: company } = useCompany();

  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  // A channel the company has not connected is off: there is no shared account behind it.
  const off = company ? [!company.whatsapp_connected && "WhatsApp", !company.calls_connected && "voice calls"].filter(Boolean) : [];

  const urgent = (data.tickets.open_by_priority.high ?? 0) + (data.tickets.open_by_priority.urgent ?? 0);
  const convToday = data.whatsapp.conversations_today;
  const aiRate = convToday ? Math.round((100 * data.whatsapp.resolved_by_ai_today) / convToday) : null;
  const callRate = data.outbound.calls_today
    ? Math.round((100 * data.outbound.completed_calls_today) / data.outbound.calls_today) : null;

  return (
    <>
      <PageHeader
        title={`${greeting()}, ${user?.full_name.split(" ")[0] ?? ""}`}
        subtitle={`Here's what's happening at ${user?.company?.name ?? "your company"} across WhatsApp support and outbound AI calls today. Updates every 15 seconds.`}
      />

      {off.length > 0 && (
        <div className="error" role="status">
          <IconAlert size={16} />
          <span>
            {off.join(" and ")} {off.length > 1 ? "are" : "is"} off for your company until {off.length > 1 ? "their accounts are" : "its account is"} connected.{" "}
            <Link to="/company">Open Company Settings</Link>
          </span>
        </div>
      )}

      <div className="card">
        <div className="section-title">
          <h2><span className="section-icon"><IconChat size={16} /></span> WhatsApp Support</h2>
          <Link to="/tickets" className="btn ghost sm">View tickets →</Link>
        </div>
        <div className="grid grid-4">
          <Stat label="Conversations today" value={convToday} icon={<IconChat size={18} />} tone="blue" />
          <Stat label="Resolved by AI today" value={data.whatsapp.resolved_by_ai_today} icon={<IconBot size={18} />} tone="green"
            hint={aiRate !== null ? `${aiRate}% of today's conversations` : undefined} />
          <Stat label="Escalated today" value={data.whatsapp.escalated_today} icon={<IconSparkle size={18} />} tone="violet" />
          <Stat label="Open conversations" value={data.whatsapp.open_conversations} icon={<IconClock size={18} />} tone="grey" />
          <Stat label="Open tickets" value={data.tickets.open} icon={<IconTicket size={18} />} tone="blue"
            hint={`${data.tickets.created_today} created today`} />
          <Stat label="Unassigned tickets" value={data.tickets.unassigned} icon={<IconUser size={18} />}
            tone={data.tickets.unassigned ? "amber" : "green"} />
          <Stat label="High / urgent open" value={urgent} icon={<IconAlert size={18} />} tone={urgent ? "red" : "green"} />
        </div>
      </div>

      <div className="card">
        <div className="section-title">
          <h2><span className="section-icon"><IconPhone size={16} /></span> Outbound AI Calls</h2>
          <Link to="/campaigns" className="btn ghost sm">View campaigns →</Link>
        </div>
        <div className="grid grid-4">
          <Stat label="Running campaigns" value={data.outbound.running_campaigns} icon={<IconSparkle size={18} />} tone="violet" />
          <Stat label="Active calls" value={data.outbound.active_calls} icon={<IconPhone size={18} />}
            tone={data.outbound.active_calls ? "green" : "grey"} />
          <Stat label="Calls today" value={data.outbound.calls_today} icon={<IconClock size={18} />} tone="blue" />
          <Stat label="Completed today" value={data.outbound.completed_calls_today} icon={<IconCheck size={18} />} tone="green"
            hint={callRate !== null ? `${callRate}% completion` : undefined} />
        </div>
        {(data.usage.daily_call_limit !== null || data.usage.max_campaigns !== null) && (
          <div className="grid grid-2" style={{ marginTop: 16 }}>
            {data.usage.daily_call_limit !== null && (
              <UsageMeter label="Daily call limit" used={data.usage.calls_today} limit={data.usage.daily_call_limit}
                hint={data.usage.calls_today >= data.usage.daily_call_limit ? "Limit reached — remaining calls continue tomorrow." : undefined} />
            )}
            {data.usage.max_campaigns !== null && (
              <UsageMeter label="Campaigns" used={data.usage.campaigns} limit={data.usage.max_campaigns} />
            )}
          </div>
        )}
      </div>
    </>
  );
}
