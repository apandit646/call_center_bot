import { useEffect } from "react";
import { AppState } from "react-native";
import { Stack, useRouter, useSegments } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/auth/AuthContext";
import { useColors } from "@/theme";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 10_000 } },
});

function Screens() {
  const { user, loading } = useAuth();
  const segments = useSegments();
  const router = useRouter();
  const c = useColors();

  // Coming back to the app refreshes what is on screen, like switching back to a browser tab.
  useEffect(() => {
    const sub = AppState.addEventListener("change", (state) => focusManager.setFocused(state === "active"));
    return () => sub.remove();
  }, []);

  // Signed out (or the login expired) while on any screen: back to the login screen.
  const onLogin = segments[0] === "login";
  useEffect(() => {
    if (!loading && !user && !onLogin) router.replace("/login");
  }, [loading, user, onLogin, router]);

  return (
    <>
      <StatusBar style="auto" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: c.panel },
          headerTintColor: c.text,
          headerShadowVisible: false,
          headerBackButtonDisplayMode: "minimal",
          contentStyle: { backgroundColor: c.bg },
        }}
      >
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="login" options={{ headerShown: false }} />
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="tickets/[id]" options={{ title: "Ticket" }} />
        <Stack.Screen name="conversations/[id]" options={{ title: "Conversation" }} />
        <Stack.Screen name="calls/[id]" options={{ title: "Call" }} />
        <Stack.Screen name="campaigns/index" options={{ title: "Campaigns" }} />
        <Stack.Screen name="campaigns/[id]" options={{ title: "Campaign" }} />
        <Stack.Screen name="customers" options={{ title: "Customers" }} />
        <Stack.Screen name="knowledge" options={{ title: "Knowledge base" }} />
        <Stack.Screen name="account" options={{ title: "Account" }} />
        <Stack.Screen name="platform/index" options={{ title: "Companies" }} />
        <Stack.Screen name="platform/[id]" options={{ title: "Company" }} />
      </Stack>
    </>
  );
}

export default function RootLayout() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Screens />
      </AuthProvider>
    </QueryClientProvider>
  );
}
