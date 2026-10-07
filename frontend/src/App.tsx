import { Route, Routes } from "react-router-dom";
import { RequireAuth, useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import TicketList from "./pages/tickets/TicketList";
import TicketDetail from "./pages/tickets/TicketDetail";
import ConversationList from "./pages/conversations/ConversationList";
import ConversationDetail from "./pages/conversations/ConversationDetail";
import InboundCalls from "./pages/conversations/InboundCalls";
import Callbacks from "./pages/conversations/Callbacks";
import Reports from "./pages/Reports";
import Billing from "./pages/Billing";
import PlatformBilling from "./pages/platform/Billing";
import Knowledge from "./pages/knowledge/Knowledge";
import CampaignList from "./pages/campaigns/CampaignList";
import CampaignNew from "./pages/campaigns/CampaignNew";
import CampaignDetail from "./pages/campaigns/CampaignDetail";
import CallDetail from "./pages/campaigns/CallDetail";
import Customers from "./pages/Customers";
import Users from "./pages/Users";
import Account from "./pages/Account";
import CompanySettings from "./pages/CompanySettings";
import Companies from "./pages/platform/Companies";
import CompanyNew from "./pages/platform/CompanyNew";
import CompanyDetail from "./pages/platform/CompanyDetail";

export default function App() {
  const { user } = useAuth();
  const managers = ["admin", "supervisor"] as const;

  // The platform owner manages companies and has no company data of their own, so they get a
  // separate, smaller portal.
  if (user?.role === "super_admin") {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><Layout /></RequireAuth>}>
          <Route index element={<Companies />} />
          <Route path="companies/new" element={<CompanyNew />} />
          <Route path="companies/:id" element={<CompanyDetail />} />
          <Route path="billing" element={<PlatformBilling />} />
          <Route path="account" element={<Account />} />
          <Route path="*" element={<div className="card">Page not found.</div>} />
        </Route>
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Dashboard />} />
        <Route path="tickets" element={<TicketList />} />
        <Route path="tickets/:id" element={<TicketDetail />} />
        <Route path="conversations" element={<ConversationList />} />
        <Route path="conversations/:id" element={<ConversationDetail />} />
        <Route path="inbound-calls" element={<InboundCalls />} />
        <Route path="callbacks" element={<Callbacks />} />
        <Route path="reports" element={<Reports />} />
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="campaigns" element={<RequireAuth roles={[...managers]}><CampaignList /></RequireAuth>} />
        <Route path="campaigns/new" element={<RequireAuth roles={[...managers]}><CampaignNew /></RequireAuth>} />
        <Route path="campaigns/:id" element={<RequireAuth roles={[...managers]}><CampaignDetail /></RequireAuth>} />
        <Route path="calls/:id" element={<RequireAuth roles={[...managers]}><CallDetail /></RequireAuth>} />
        <Route path="customers" element={<Customers />} />
        <Route path="users" element={<RequireAuth roles={[...managers]}><Users /></RequireAuth>} />
        <Route path="company" element={<CompanySettings />} />
        <Route path="billing" element={<RequireAuth roles={["admin"]}><Billing /></RequireAuth>} />
        <Route path="account" element={<Account />} />
        <Route path="*" element={<div className="card">Page not found.</div>} />
      </Route>
    </Routes>
  );
}
