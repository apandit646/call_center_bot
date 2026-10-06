import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, onForcedLogout, tokens } from "@/api/client";
import type { Role, TokenPair, User } from "@/api/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  hasRole: (...roles: Role[]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    void tokens.clear();
    setUser(null);
    qc.clear(); // the next person to sign in on this phone must not see this one's data
  }, [qc]);

  useEffect(() => {
    (async () => {
      await tokens.load();
      if (tokens.access) {
        try {
          setUser((await api.get<User>("/auth/me")).data);
        } catch {
          /* the saved login is no longer valid, or the server is unreachable: show the login screen */
        }
      }
      setLoading(false);
    })();
  }, []);

  // The API client reports when a refresh token is rejected.
  useEffect(() => onForcedLogout(() => setUser(null)), []);

  const login = async (email: string, password: string) => {
    const { data } = await api.post<TokenPair>("/auth/login", { email, password });
    await tokens.set(data);
    setUser(data.user);
  };

  const hasRole = (...roles: Role[]) => !!user && roles.includes(user.role);

  return <AuthContext.Provider value={{ user, loading, login, logout, hasRole }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
