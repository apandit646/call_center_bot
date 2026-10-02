import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import {
  IconBook, IconBuilding, IconChat, IconContacts, IconDashboard, IconLogout, IconMenu, IconPhone, IconSettings,
  IconTicket, IconUser, IconUsers,
} from "./Icons";
import { Avatar } from "./ui";
import { ThemeSwitch } from "../theme";

export default function Layout() {
  const { user, logout, hasRole } = useAuth();
  const manager = hasRole("admin", "supervisor");
  const platform = hasRole("super_admin");
  // Company users see their company's name; the platform owner sees the platform's.
  const workspace = platform ? "Platform Admin" : user?.company?.name ?? "Customer Platform";
  const [open, setOpen] = useState(false);
  const location = useLocation();

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
            <div className="brand-sub">{platform ? "Companies & limits" : "Support & Voice AI"}</div>
          </div>
        </div>

        {platform ? (
          <nav>
            <div className="nav-section">Platform</div>
            <NavLink to="/" end><IconBuilding /> Companies</NavLink>
            <NavLink to="/companies/new"><IconSettings /> Register company</NavLink>
            <div className="nav-section">You</div>
            <NavLink to="/account"><IconUser /> My Account</NavLink>
          </nav>
        ) : (
        <nav>
          <NavLink to="/" end><IconDashboard /> Dashboard</NavLink>

          <div className="nav-section">WhatsApp Support</div>
          <NavLink to="/tickets"><IconTicket /> Tickets</NavLink>
          <NavLink to="/conversations"><IconChat /> Conversations</NavLink>
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
          <NavLink to="/account"><IconUser /> My Account</NavLink>
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
        <Outlet />
      </main>
    </div>
  );
}
