import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";
import * as SecureStore from "expo-secure-store";
import type { TokenPair } from "./types";

const ACCESS_KEY = "access_token";
const REFRESH_KEY = "refresh_token";
const SERVER_KEY = "server_url";

/** "192.168.1.20:8000" or "https://x.trycloudflare.com/" -> the API's base URL. */
export function apiUrl(server: string): string {
  let url = server.trim().replace(/\/+$/, "");
  if (!url) return "";
  if (!/^https?:\/\//i.test(url)) {
    // A bare IP address or "localhost" is a machine on the local network, which serves plain http.
    url = (/^(\d{1,3}\.){3}\d{1,3}(:\d+)?$|^localhost(:\d+)?$/i.test(url) ? "http://" : "https://") + url;
  }
  return /\/api\/v1$/.test(url) ? url : `${url}/api/v1`;
}

// Kept in memory so every request can read them without waiting; the phone's secure storage holds the
// copy that survives closing the app.
let access: string | null = null;
let refresh: string | null = null;
let serverAddress = process.env.EXPO_PUBLIC_API_URL ?? "";

export const tokens = {
  get access() {
    return access;
  },
  /** Read what was saved the last time the app was open. Call once at start-up. */
  async load() {
    try {
      [access, refresh] = await Promise.all([SecureStore.getItemAsync(ACCESS_KEY), SecureStore.getItemAsync(REFRESH_KEY)]);
      serverAddress = (await SecureStore.getItemAsync(SERVER_KEY)) || serverAddress;
    } catch {
      /* secure storage unavailable: the user simply signs in again */
    }
  },
  async set(pair: Pick<TokenPair, "access_token" | "refresh_token">) {
    access = pair.access_token;
    refresh = pair.refresh_token;
    await Promise.all([SecureStore.setItemAsync(ACCESS_KEY, access), SecureStore.setItemAsync(REFRESH_KEY, refresh)]).catch(() => {});
  },
  async clear() {
    access = refresh = null;
    await Promise.all([SecureStore.deleteItemAsync(ACCESS_KEY), SecureStore.deleteItemAsync(REFRESH_KEY)]).catch(() => {});
  },
};

export const server = {
  /** What the user typed (or EXPO_PUBLIC_API_URL). */
  get address() {
    return serverAddress;
  },
  get apiUrl() {
    return apiUrl(serverAddress);
  },
  async set(address: string) {
    serverAddress = address.trim();
    await SecureStore.setItemAsync(SERVER_KEY, serverAddress).catch(() => {});
  },
};

// The API client calls these when a refresh token is rejected.
const logoutListeners = new Set<() => void>();
export function onForcedLogout(listener: () => void) {
  logoutListeners.add(listener);
  return () => void logoutListeners.delete(listener);
}

export const api = axios.create({ timeout: 30_000 });

api.interceptors.request.use((config) => {
  config.baseURL = server.apiUrl;
  if (access) config.headers.Authorization = `Bearer ${access}`;
  return config;
});

// One refresh request shared by every call that got a 401 at the same time.
let refreshing: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (!refresh) return null;
  try {
    const { data } = await axios.post<TokenPair>(`${server.apiUrl}/auth/refresh`, { refresh_token: refresh }, { timeout: 30_000 });
    await tokens.set(data);
    return data.access_token;
  } catch {
    return null;
  }
}

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const original = error.config as (InternalAxiosRequestConfig & { _retried?: boolean }) | undefined;
    const isAuthCall = original?.url?.includes("/auth/login") || original?.url?.includes("/auth/refresh");
    if (error.response?.status === 401 && original && !original._retried && !isAuthCall) {
      original._retried = true;
      refreshing ??= refreshAccessToken().finally(() => {
        refreshing = null;
      });
      const newToken = await refreshing;
      if (newToken) {
        original.headers.Authorization = `Bearer ${newToken}`;
        return api(original);
      }
      await tokens.clear();
      logoutListeners.forEach((listener) => listener());
    }
    return Promise.reject(error);
  },
);

export function errorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    if (!err.response) {
      return server.address
        ? `Cannot reach the server at ${server.address}. Check the server address and that the phone is online.`
        : "No server address is set. Enter it on the login screen.";
    }
    const detail = (err.response.data as { detail?: unknown } | undefined)?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d: { loc?: string[]; msg?: string }) => `${d.loc?.slice(1).join(".")}: ${d.msg}`).join("; ");
    }
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

/** Drop empty values so they aren't sent as query params. */
export function cleanParams(params: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v !== undefined && v !== null && v !== false));
}
