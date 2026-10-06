import type { ComponentProps } from "react";
import type { ColorValue } from "react-native";
import { Redirect } from "expo-router";
import { Tabs } from "expo-router/js-tabs";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "@/auth/AuthContext";
import { useColors } from "@/theme";

const icon = (name: ComponentProps<typeof Ionicons>["name"]) =>
  function TabIcon({ color, size }: { color: ColorValue; size: number }) {
    return <Ionicons name={name} color={color} size={size} />;
  };

/** The company portal: what a company's own people use every day. */
export default function CompanyTabs() {
  const { user } = useAuth();
  const c = useColors();
  if (!user) return <Redirect href="/login" />;
  if (user.role === "super_admin") return <Redirect href="/platform" />;
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: c.panel },
        headerTintColor: c.text,
        headerShadowVisible: false,
        tabBarActiveTintColor: c.primary,
        tabBarInactiveTintColor: c.muted,
        tabBarStyle: { backgroundColor: c.panel, borderTopColor: c.border },
        sceneStyle: { backgroundColor: c.bg },
      }}
    >
      <Tabs.Screen name="dashboard" options={{ title: "Dashboard", tabBarIcon: icon("speedometer-outline") }} />
      <Tabs.Screen name="tickets" options={{ title: "Tickets", tabBarIcon: icon("pricetags-outline") }} />
      <Tabs.Screen name="chats" options={{ title: "Chats", tabBarIcon: icon("chatbubbles-outline") }} />
      <Tabs.Screen name="calls" options={{ title: "Calls", tabBarIcon: icon("call-outline") }} />
      <Tabs.Screen name="more" options={{ title: "More", tabBarIcon: icon("menu-outline") }} />
    </Tabs>
  );
}
