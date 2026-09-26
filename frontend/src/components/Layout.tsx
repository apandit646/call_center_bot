import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Badge } from "./ui";

export default function Layout() {
  const { user, logout, hasRole } = useAuth();
  const manager = hasRole("admin", "supervisor");

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">AI Customer Platform</div>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>

          <div className="nav-section">WhatsApp Support</div>
          <NavLink to="/tickets">Tickets</NavLink>
          <NavLink to="/conversations">Conversations</NavLink>
          <NavLink to="/knowledge">Knowledge Base</NavLink>

          {manager && (
            <>
              <div className="nav-section">Outbound AI Q&amp;A</div>
              <NavLink to="/campaigns">Campaigns</NavLink>
            </>
          )}

          <div className="nav-section">Shared</div>
          <NavLink to="/customers">Customers</NavLink>
          {manager && <NavLink to="/users">Users &amp; Agents</NavLink>}
          <NavLink to="/account">My Account</NavLink>
        </nav>
        <div className="sidebar-foot">
          <div>{user?.full_name}</div>
          <div className="small muted">{user?.email}</div>
          {user && <Badge value={user.role} />}
          <button className="btn ghost block" onClick={logout}>Sign out</button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
