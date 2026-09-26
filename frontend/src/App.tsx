import { Route, Routes } from "react-router-dom";
import { RequireAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import TicketList from "./pages/tickets/TicketList";
import TicketDetail from "./pages/tickets/TicketDetail";
import ConversationList from "./pages/conversations/ConversationList";
import ConversationDetail from "./pages/conversations/ConversationDetail";
import Knowledge from "./pages/knowledge/Knowledge";
import CampaignList from "./pages/campaigns/CampaignList";
import CampaignNew from "./pages/campaigns/CampaignNew";
import CampaignDetail from "./pages/campaigns/CampaignDetail";
import CallDetail from "./pages/campaigns/CallDetail";
import Customers from "./pages/Customers";
import Users from "./pages/Users";
import Account from "./pages/Account";

export default function App() {
  const managers = ["admin", "supervisor"] as const;
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<Dashboard />} />
        <Route path="tickets" element={<TicketList />} />
        <Route path="tickets/:id" element={<TicketDetail />} />
        <Route path="conversations" element={<ConversationList />} />
        <Route path="conversations/:id" element={<ConversationDetail />} />
        <Route path="knowledge" element={<Knowledge />} />
        <Route path="campaigns" element={<RequireAuth roles={[...managers]}><CampaignList /></RequireAuth>} />
        <Route path="campaigns/new" element={<RequireAuth roles={[...managers]}><CampaignNew /></RequireAuth>} />
        <Route path="campaigns/:id" element={<RequireAuth roles={[...managers]}><CampaignDetail /></RequireAuth>} />
        <Route path="calls/:id" element={<RequireAuth roles={[...managers]}><CallDetail /></RequireAuth>} />
        <Route path="customers" element={<Customers />} />
        <Route path="users" element={<RequireAuth roles={[...managers]}><Users /></RequireAuth>} />
        <Route path="account" element={<Account />} />
        <Route path="*" element={<div className="card">Page not found.</div>} />
      </Route>
    </Routes>
  );
}
