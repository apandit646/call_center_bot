import { customerContact, customerLabel } from "@/api/types";
import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";
import type { Conversation } from "@/api/types";
import { Avatar, Badge, Chips, Input, ListRow, PagedList, Row, T, fmtDate } from "@/components/ui";
import { useDebounced } from "@/hooks";

const STATUS = [["", "All"], ["open", "Open"], ["escalated", "Escalated"], ["resolved", "Resolved"], ["closed", "Closed"]] as const;

const CHANNEL = [["whatsapp", "WhatsApp"], ["email", "Email"]] as const;

/** WhatsApp and email conversations. Phone calls have their own tab. */
export default function Chats() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<(typeof STATUS)[number][0]>("");
  const [channel, setChannel] = useState<(typeof CHANNEL)[number][0]>("whatsapp");

  return (
    <PagedList<Conversation>
      queryKey={["conversations"]}
      url="/conversations"
      params={{ q: useDebounced(q), status, channel }}
      refetchInterval={15_000}
      empty="No conversations match these filters."
      header={
        <>
          <Input placeholder="Search customer name or number…" value={q} onChangeText={setQ} autoCorrect={false} returnKeyType="search" />
          <Chips options={CHANNEL} value={channel} onChange={setChannel} />
          <Chips options={STATUS} value={status} onChange={setStatus} />
        </>
      }
      renderItem={(c) => (
        <ListRow onPress={() => router.push(`/conversations/${c.id}`)}>
          <View style={s.line}>
            <Avatar name={customerLabel(c.customer)} />
            <View style={s.fill}>
              <Row spread>
                <T bold>{customerLabel(c.customer)}</T>
                <T small muted>{fmtDate(c.last_message_at)}</T>
              </Row>
              <T small muted>{customerContact(c.customer)}{c.domain ? ` · ${c.domain.name}` : ""}</T>
            </View>
          </View>
          {c.summary && <T lines={2}>{c.summary}</T>}
          <Row><Badge value={c.status} /></Row>
        </ListRow>
      )}
    />
  );
}

const s = StyleSheet.create({
  fill: { flex: 1 },
  line: { flexDirection: "row", gap: 10, alignItems: "center" },
});
