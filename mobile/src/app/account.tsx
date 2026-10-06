import { useState } from "react";
import { Alert } from "react-native";
import { useMutation } from "@tanstack/react-query";
import { api, server } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { Badge, Button, Card, ErrorBox, H2, Input, KV, Notice, Screen, T } from "@/components/ui";

export default function Account() {
  const { user, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmPw, setConfirmPw] = useState("");

  const change = useMutation({
    mutationFn: async () => api.post("/auth/change-password", { current_password: current, new_password: next }),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setConfirmPw("");
    },
  });
  const mismatch = !!confirmPw && next !== confirmPw;
  const confirmLogout = () =>
    Alert.alert("Sign out?", "You will need your email and password to sign in again.", [
      { text: "Cancel", style: "cancel" },
      { text: "Sign out", style: "destructive", onPress: logout },
    ]);

  return (
    <Screen>
      <Card>
        <T bold>{user?.full_name}</T>
        <KV k="Email">{user?.email ?? ""}</KV>
        <KV k="Company">{user?.company ? user.company.name : "Platform administrator"}</KV>
        <KV k="Role"><Badge value={user?.role ?? "agent"} /></KV>
        <KV k="Server">{server.address}</KV>
      </Card>

      <Card style={{ gap: 12 }}>
        <H2>Change password</H2>
        <ErrorBox error={change.error} />
        {change.isSuccess && <Notice tone="green">Password updated.</Notice>}
        <Input label="Current password" value={current} onChangeText={setCurrent} secureTextEntry autoCapitalize="none" autoComplete="current-password" />
        <Input label="New password (at least 8 characters)" value={next} onChangeText={setNext} secureTextEntry autoCapitalize="none" autoComplete="new-password" />
        <Input label="Confirm new password" value={confirmPw} onChangeText={setConfirmPw} secureTextEntry autoCapitalize="none" autoComplete="new-password" />
        {mismatch && <T small muted>The two new passwords are not the same.</T>}
        <Button title="Update password" busy={change.isPending} disabled={!current || next.length < 8 || next !== confirmPw} onPress={() => change.mutate()} />
      </Card>

      <Button variant="danger" title="Sign out" onPress={confirmLogout} />
    </Screen>
  );
}
