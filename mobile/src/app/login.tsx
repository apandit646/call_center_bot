import { useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { apiUrl, server } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { Button, Card, ErrorBox, Input, T } from "@/components/ui";
import { useColors } from "@/theme";

export default function Login() {
  const { login } = useAuth();
  const router = useRouter();
  const c = useColors();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [address, setAddress] = useState(server.address);
  // The server address is asked for only when it is missing or the person wants to change it.
  const [showServer, setShowServer] = useState(!server.address);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await server.set(address);
      await login(email.trim(), password);
      router.replace("/");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={[s.fill, { backgroundColor: c.bg }]}>
      <KeyboardAvoidingView style={s.fill} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={s.page} keyboardShouldPersistTaps="handled">
          <View style={s.brand}>
            <Text style={[s.title, { color: c.text }]}>AI Customer Platform</Text>
            <T muted>Sign in with the same email and password as the web portal.</T>
          </View>
          <Card style={s.form}>
            <ErrorBox error={error} />
            <Input
              label="Email" value={email} onChangeText={setEmail} autoCapitalize="none" autoCorrect={false}
              keyboardType="email-address" autoComplete="email" textContentType="username" placeholder="you@company.com"
            />
            <Input
              label="Password" value={password} onChangeText={setPassword} secureTextEntry autoCapitalize="none"
              autoComplete="current-password" textContentType="password" onSubmitEditing={submit} returnKeyType="go"
            />
            {showServer && (
              <>
                <Input
                  label="Server address" value={address} onChangeText={setAddress} autoCapitalize="none" autoCorrect={false}
                  keyboardType="url" placeholder="https://your-server.example.com"
                />
                <T small muted>
                  The address where the platform runs, as this phone can reach it: your tunnel address, or the PC&apos;s Wi-Fi
                  address such as 192.168.1.20:8000. &quot;localhost&quot; does not work on a phone.
                  {address.trim() ? `\nThe app will use: ${apiUrl(address)}` : ""}
                </T>
              </>
            )}
            <Button title="Sign in" onPress={submit} busy={busy} disabled={!email.trim() || !password || !address.trim()} />
            {!showServer && <Button small variant="ghost" title={`Server: ${server.address} — change`} onPress={() => setShowServer(true)} />}
          </Card>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  fill: { flex: 1 },
  page: { flexGrow: 1, justifyContent: "center", padding: 20, gap: 18 },
  brand: { gap: 6 },
  title: { fontSize: 26, fontWeight: "800" },
  form: { gap: 12, padding: 16 },
});
