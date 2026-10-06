import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { IconBook, IconChat, IconGlobe, IconPhone } from "../components/Icons";
import { ErrorBox } from "../components/ui";
import { ThemeSwitch } from "../theme";

const FEATURES = [
  { icon: <IconChat />, title: "WhatsApp support that answers", text: "Domain-specific RAG answers customers and raises tickets when it can't." },
  { icon: <IconPhone />, title: "AI voice surveys at scale", text: "Upload an Excel sheet, schedule, and the AI calls, asks and records every answer." },
  { icon: <IconGlobe />, title: "Hindi & English, naturally", text: "Calls follow the customer's language, switching to Hindi the moment they do." },
  { icon: <IconBook />, title: "One knowledge base", text: "Billing, refund, technical and account docs, searched only within their domain." },
];

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (user) return <Navigate to={from} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <section className="login-hero">
        <div className="row">
          <img className="brand-mark" src="/logo.png" alt="AI Customer Platform logo" />
          <div>
            <div className="brand-name">Customer Platform</div>
            <div className="brand-sub">Support &amp; Voice AI</div>
          </div>
        </div>
        <div>
          <h1>Every customer conversation, <span className="gradient-text">handled by AI.</span></h1>
          <p>WhatsApp support and outbound voice calls in one console, with your team stepping in only when it matters.</p>
        </div>
        <div className="login-features">
          {FEATURES.map((f) => (
            <div className="login-feature" key={f.title}>
              {f.icon}
              <div><strong>{f.title}</strong><span>{f.text}</span></div>
            </div>
          ))}
        </div>
      </section>

      <section className="login-panel">
        <div className="theme-corner"><ThemeSwitch onLight /></div>
        <form className="login-card form" onSubmit={submit}>
          <div>
            <img className="brand-mark" src="/logo.png" alt="AI Customer Platform logo" />
            <h1>Welcome back</h1>
            <p className="muted" style={{ margin: "6px 0 0" }}>Sign in with your company or platform account.</p>
          </div>
          <ErrorBox error={error} />
          <label>
            Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus
              placeholder="you@company.com" autoComplete="username" />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
              placeholder="••••••••" autoComplete="current-password" />
          </label>
          <button className="btn block lg" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </section>
    </div>
  );
}
