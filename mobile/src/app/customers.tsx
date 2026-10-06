import { useState } from "react";
import { StyleSheet, View } from "react-native";
import type { Customer } from "@/api/types";
import { Avatar, Input, ListRow, PagedList, T } from "@/components/ui";
import { useDebounced } from "@/hooks";

export default function Customers() {
  const [q, setQ] = useState("");
  return (
    <PagedList<Customer>
      queryKey={["customers"]}
      url="/customers"
      params={{ q: useDebounced(q) }}
      empty="No customers match this search."
      header={<Input placeholder="Search name, phone or email…" value={q} onChangeText={setQ} autoCorrect={false} returnKeyType="search" />}
      renderItem={(c) => (
        <ListRow>
          <View style={s.line}>
            <Avatar name={c.name ?? c.phone} />
            <View style={s.fill}>
              <T bold selectable>{c.name ?? "—"}</T>
              <T small muted selectable>{[c.phone, c.email, c.city].filter(Boolean).join(" · ")}</T>
            </View>
          </View>
        </ListRow>
      )}
    />
  );
}

const s = StyleSheet.create({
  fill: { flex: 1 },
  line: { flexDirection: "row", gap: 10, alignItems: "center" },
});
