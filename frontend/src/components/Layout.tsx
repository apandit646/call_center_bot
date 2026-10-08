import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import {
  IconAlert, IconBook, IconBuilding, IconCard, IconChart, IconChat, IconClock, IconContacts, IconDashboard, IconLogout, IconMenu, IconPhone, IconSettings,
  IconTicket, IconUser, IconUsers,
} from "./Icons";
import ErrorBoundary from "./ErrorBoundary";
import { Avatar } from "./ui";
import { useCompany } from "../hooks";
import { useIssueSummary } from "../pages/Issues";
import { ThemeSwitch } from "../theme";

export default function Layout() {
  const { user, logout, hasRole } = useAuth();
  const manager = hasRole("admin", "supervisor");
  const platform = hasRole("super_admin");
  // Company users see their company's name; the platform owner sees the platform's.
  const workspace = platform ? "Platform Admin" : user?.company?.name ?? "Customer Platform";
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const { data: company } = useCompany(!!user && !platform);
  const { data: issues } = useIssueSummary(!!user);

  useEffect(() => setOpen(false), [location.pathname]); // close the mobile menu after navigating

  return (
    <div className={`shell ${open ? "menu-open" : ""}`}>
      <header className="topbar">
        <button className="icon-btn" onClick={() => setOpen((o) => !o)} aria-label="Toggle menu"><IconMenu /></button>
        <span className="brand-name">{workspace}</span>
      </header>

      <aside className="sidebar">
        <div className="brand">
          <img className="brand-mark" src="/logo.png" alt="AI Customer Platform logo" />
          <div>
            <div className="brand-name">{workspace}</div>
            <div className="brand-sub">{platform ? "All companies" : "Support & Voice AI"}</div>
          </div>
        </div>

        {platform ? (
          <nav>
            <div className="nav-section">Platform</div>
            <NavLink to="/" end><IconDashboard /> Dashboard</NavLink>
            <NavLink to="/reports"><IconChart /> Reports</NavLink>
            <NavLink to="/companies" end><IconBuilding /> Companies</NavLink>
            <NavLink to="/billing"><IconCard /> Billing</NavLink>
            <NavLink to="/issues"><IconAlert /> Issue reports{issues && issues.inbox_open > 0 && <span className="nav-count">{issues.inbox_open}</span>}</NavLink>
            <NavLink to="/companies/new"><IconSettings /> Register company</NavLink>
            <div className="nav-section">You</div>
            <NavLink to="/account"><IconUser /> My Account</NavLink>
          </nav>
        ) : (
        <nav>
          <NavLink to="/" end><IconDashboard /> Dashboard</NavLink>
          <NavLink to="/reports"><IconChart /> Reports</NavLink>

          <div className="nav-section">Customer Support</div>
          <NavLink to="/tickets"><IconTicket /> Tickets</NavLink>
          <NavLink to="/conversations"><IconChat /> Conversations</NavLink>
          <NavLink to="/inbound-calls"><IconPhone /> Inbound Calls</NavLink>
          <NavLink to="/callbacks"><IconClock /> Callbacks</NavLink>
          <NavLink to="/knowledge"><IconBook /> Knowledge Base</NavLink>

          {manager && (
            <>
              <div className="nav-section">Outbound AI Calls</div>
              <NavLink to="/campaigns"><IconPhone /> Campaigns</NavLink>
            </>
          )}

          <div className="nav-section">Shared</div>
          <NavLink to="/customers"><IconContacts /> Customers</NavLink>
          {manager && <NavLink to="/users"><IconUsers /> Users &amp; Agents</NavLink>}
          <NavLink to="/company"><IconBuilding /> Company Settings</NavLink>
          {hasRole("admin") && <NavLink to="/billing"><IconCard /> Billing</NavLink>}
          <NavLink to="/account"><IconUser /> My Account</NavLink>
          <NavLink to="/issues"><IconAlert /> Report an issue{issues && issues.inbox_open > 0 && <span className="nav-count">{issues.inbox_open}</span>}</NavLink>
        </nav>
        )}

        <div className="sidebar-bottom">
          <ThemeSwitch />
          <div className="sidebar-foot">
            <Avatar name={user?.full_name} size={36} />
            <div className="sidebar-user">
              <div className="sidebar-user-name">{user?.full_name}</div>
              <div className="sidebar-user-role">{user?.role.replace("_", " ")}</div>
            </div>
            <button className="icon-btn dark" onClick={logout} title="Sign out" aria-label="Sign out"><IconLogout /></button>
          </div>
        </div>
      </aside>
      <div className="scrim" onClick={() => setOpen(false)} />

      <main className="content">
        {/* A page that fails to draw shows a message here instead of blanking the whole app. */}
        {company?.billing_hold && (
          <div className="error" role="alert">
            <span>
              An invoice is overdue, so campaigns and callbacks are on hold. Inbound calls, WhatsApp and email still work.{" "}
              {hasRole("admin") ? <NavLink to="/billing">Open Billing to pay</NavLink> : "Ask your company admin to pay it."}
            </span>
          </div>
        )}
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
