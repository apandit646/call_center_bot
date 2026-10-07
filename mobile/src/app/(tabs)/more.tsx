import type { ComponentProps } from "react";
import { Alert, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "@/auth/AuthContext";
import { Avatar, Badge, Button, Card, ListRow, Row, Screen, T } from "@/components/ui";
import { useIssueSummary } from "@/issues";
import { useColors } from "@/theme";

function Item({ icon, title, hint, to }: { icon: ComponentProps<typeof Ionicons>["name"]; title: string; hint: string; to: string }) {
  const router = useRouter();
  const c = useColors();
  return (
    <ListRow onPress={() => router.push(to)}>
      <View style={s.line}>
        <Ionicons name={icon} size={22} color={c.primary} />
        <View style={s.fill}>
          <T bold>{title}</T>
          <T small muted>{hint}</T>
        </View>
        <Ionicons name="chevron-forward" size={18} color={c.muted} />
      </View>
    </ListRow>
  );
}

export default function More() {
  const { user, logout, hasRole } = useAuth();
  const { data: issues } = useIssueSummary();
  const confirmLogout = () =>
    Alert.alert("Sign out?", "You will need your email and password to sign in again.", [
      { text: "Cancel", style: "cancel" },
      { text: "Sign out", style: "destructive", onPress: logout },
    ]);

  return (
    <Screen>
      <Card>
        <View style={s.line}>
          <Avatar name={user?.full_name} size={46} />
          <View style={s.fill}>
            <T bold>{user?.full_name}</T>
            <T small muted>{user?.email}</T>
            <Row><Badge value={user?.role ?? "agent"} /></Row>
          </View>
        </View>
      </Card>

      {hasRole("admin", "supervisor") && (
        <Item icon="megaphone-outline" title="Campaigns" hint="Progress, calls and results; pause or resume" to="/campaigns" />
      )}
      <Item icon="time-outline" title="Callbacks" hint="Customers who asked to be called back, and when" to="/callbacks" />
      <Item icon="bar-chart-outline" title="Reports" hint="Tickets per day, AI-resolved rate, calls by outcome" to="/reports" />
      <Item icon="people-outline" title="Customers" hint="Everyone who called, wrote or was called" to="/customers" />
      <Item icon="library-outline" title="Knowledge base" hint="Documents, and the questions the AI could not answer" to="/knowledge" />
      {hasRole("admin") && <Item icon="card-outline" title="Billing" hint="Your prices, this month so far, invoices" to="/billing" />}
      <Item icon="alert-circle-outline" title="Report an issue"
        hint={issues?.inbox_open ? `${issues.inbox_open} from your team waiting for you` : `Goes to ${issues?.reports_go_to ?? "your company admin"}`} to="/issues" />
      <Item icon="person-circle-outline" title="Account" hint="Change your password" to="/account" />

      <T small muted>
        Setting up calling, WhatsApp and email, creating or re-running campaigns, uploading documents and managing users are done on the web portal.
      </T>
      <Button variant="danger" title="Sign out" onPress={confirmLogout} />
    </Screen>
  );
}

const s = StyleSheet.create({
  fill: { flex: 1 },
  line: { flexDirection: "row", gap: 12, alignItems: "center" },
});
