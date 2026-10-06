import type { ReactNode } from "react";
import {
  ActivityIndicator, FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View,
  type StyleProp, type TextInputProps, type TextStyle, type ViewStyle,
} from "react-native";
import { useInfiniteQuery } from "@tanstack/react-query";
import { api, cleanParams, errorMessage } from "@/api/client";
import type { Page } from "@/api/types";
import { useColors, type Colors } from "@/theme";

export type Tone = "blue" | "green" | "amber" | "red" | "grey" | "violet";

// Same value -> colour mapping as the web portal's badges.
const TONE: Record<string, Tone> = {
  open: "blue", in_progress: "amber", pending_customer: "amber", resolved: "green", closed: "grey",
  escalated: "red", low: "grey", medium: "blue", high: "amber", urgent: "red",
  draft: "grey", scheduled: "blue", running: "green", paused: "amber", completed: "green", cancelled: "grey",
  pending: "grey", processing: "amber", ready: "green", failed: "red",
  initiated: "blue", ringing: "blue", no_answer: "amber", busy: "amber", canceled: "grey",
  answered: "green", unclear: "amber", skipped: "grey", do_not_call: "grey",
  super_admin: "red", admin: "violet", supervisor: "amber", agent: "blue",
  active: "green", suspended: "red", connected: "green", not_connected: "grey", on: "green", off: "grey",
  whatsapp: "green", phone: "blue", manual: "grey",
  ticket_raised: "amber", ticket_updated: "blue", abandoned: "grey",
};

const toneColors = (c: Colors, tone: Tone) => ({ fg: c[tone], bg: c[`${tone}Soft`] });

export const label = (value: string) => value.replace(/_/g, " ");

// ------------------------------------------------------------------ text
export function T({ children, muted, small, bold, style, lines, selectable }: {
  children: ReactNode; muted?: boolean; small?: boolean; bold?: boolean; style?: StyleProp<TextStyle>; lines?: number; selectable?: boolean;
}) {
  const c = useColors();
  return (
    <Text
      numberOfLines={lines}
      selectable={selectable}
      style={[{ color: muted ? c.muted : c.text, fontSize: small ? 12.5 : 15, lineHeight: small ? 17 : 21 }, bold && s.bold, style]}
    >
      {children}
    </Text>
  );
}

export function H2({ children }: { children: ReactNode }) {
  const c = useColors();
  return <Text style={[s.h2, { color: c.text }]}>{children}</Text>;
}

// ------------------------------------------------------------------ layout
/** A scrolling page with pull-to-refresh. Lists use PagedList instead. */
export function Screen({ children, refreshing, onRefresh, footer }: {
  children: ReactNode; refreshing?: boolean; onRefresh?: () => void; footer?: ReactNode;
}) {
  const c = useColors();
  return (
    <View style={[s.fill, { backgroundColor: c.bg }]}>
      <ScrollView
        contentContainerStyle={s.page}
        keyboardShouldPersistTaps="handled"
        refreshControl={onRefresh ? <RefreshControl refreshing={!!refreshing} onRefresh={onRefresh} tintColor={c.primary} /> : undefined}
      >
        {children}
      </ScrollView>
      {footer}
    </View>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  const c = useColors();
  return <View style={[s.card, { backgroundColor: c.panel, borderColor: c.border }, style]}>{children}</View>;
}

export function Row({ children, spread, style }: { children: ReactNode; spread?: boolean; style?: StyleProp<ViewStyle> }) {
  return <View style={[s.row, spread && s.spread, style]}>{children}</View>;
}

/** A label and its value on one line, for detail cards. */
export function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <View style={s.kv}>
      <T muted small style={s.kvKey}>{k}</T>
      <View style={s.fill}>{typeof children === "string" || typeof children === "number" ? <T selectable>{children}</T> : children}</View>
    </View>
  );
}

// ------------------------------------------------------------------ small pieces
export function Badge({ value, tone }: { value: string; tone?: Tone }) {
  const { fg, bg } = toneColors(useColors(), tone ?? TONE[value] ?? "grey");
  return (
    <View style={[s.badge, { backgroundColor: bg }]}>
      <View style={[s.dot, { backgroundColor: fg }]} />
      <Text style={[s.badgeText, { color: fg }]}>{label(value)}</Text>
    </View>
  );
}

export function Avatar({ name, size = 38 }: { name: string | null | undefined; size?: number }) {
  const c = useColors();
  const initials = (name ?? "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
  return (
    <View style={[s.avatar, { width: size, height: size, borderRadius: size / 2, backgroundColor: c.primarySoft }]}>
      <Text style={{ color: c.primaryText, fontWeight: "700", fontSize: size * 0.38 }}>{initials}</Text>
    </View>
  );
}

export function Button({ title, onPress, variant = "primary", disabled, busy, small }: {
  title: string; onPress: () => void; variant?: "primary" | "ghost" | "danger"; disabled?: boolean; busy?: boolean; small?: boolean;
}) {
  const c = useColors();
  const bg = variant === "primary" ? c.primary : variant === "danger" ? c.redSoft : c.panel;
  const fg = variant === "primary" ? c.onPrimary : variant === "danger" ? c.red : c.text;
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled || busy}
      onPress={onPress}
      style={({ pressed }) => [
        s.btn, small && s.btnSmall,
        { backgroundColor: bg, borderColor: variant === "ghost" ? c.border : bg, opacity: disabled || busy ? 0.5 : pressed ? 0.8 : 1 },
      ]}
    >
      {busy ? <ActivityIndicator size="small" color={fg} /> : <Text style={[s.btnText, small && s.btnTextSmall, { color: fg }]}>{title}</Text>}
    </Pressable>
  );
}

export function Input({ label: caption, style, ...props }: TextInputProps & { label?: string }) {
  const c = useColors();
  return (
    <View style={s.field}>
      {caption && <T small muted bold>{caption}</T>}
      <TextInput
        placeholderTextColor={c.muted}
        {...props}
        style={[s.input, { backgroundColor: c.inputBg, borderColor: c.border, color: c.text }, props.multiline && s.multiline, style]}
      />
    </View>
  );
}

/** One-of-many filter chips in a row that scrolls sideways. "" = no filter. */
export function Chips<V extends string>({ options, value, onChange }: {
  options: readonly (readonly [V, string])[]; value: V; onChange: (v: V) => void;
}) {
  const c = useColors();
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips} keyboardShouldPersistTaps="handled">
      {options.map(([v, text]) => {
        const on = v === value;
        return (
          <Pressable
            key={v}
            accessibilityRole="button"
            accessibilityState={{ selected: on }}
            onPress={() => onChange(v)}
            style={[s.chip, { backgroundColor: on ? c.primary : c.panel, borderColor: on ? c.primary : c.border }]}
          >
            <Text style={{ color: on ? c.onPrimary : c.text2, fontSize: 13, fontWeight: "600" }}>{text}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const c = useColors();
  if (!error) return null;
  return (
    <View accessibilityRole="alert" style={[s.note, { backgroundColor: c.redSoft }]}>
      <Text style={{ color: c.red, fontSize: 14 }}>{errorMessage(error)}</Text>
    </View>
  );
}

export function Notice({ children, tone = "blue" }: { children: ReactNode; tone?: Tone }) {
  const { fg, bg } = toneColors(useColors(), tone);
  return (
    <View style={[s.note, { backgroundColor: bg }]}>
      <Text style={{ color: fg, fontSize: 14 }}>{children}</Text>
    </View>
  );
}

export function Loading() {
  const c = useColors();
  return (
    <View style={[s.center, { backgroundColor: c.bg }]}>
      <ActivityIndicator color={c.primary} />
    </View>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <View style={s.empty}>
      <T muted style={s.centerText}>{children}</T>
    </View>
  );
}

export function Stat({ label: caption, value, hint, tone = "blue" }: { label: string; value: ReactNode; hint?: string; tone?: Tone }) {
  const c = useColors();
  return (
    <View style={[s.stat, { backgroundColor: c.panel, borderColor: c.border }]}>
      <T small muted>{caption}</T>
      <Text style={[s.statValue, { color: c[tone] }]}>{value}</Text>
      {hint && <T small muted>{hint}</T>}
    </View>
  );
}

/** Usage against a limit set by the platform admin. limit null = unlimited. */
export function UsageMeter({ label: caption, used, limit }: { label: string; used: number; limit: number | null }) {
  const c = useColors();
  const pct = limit ? Math.min(100, Math.round((100 * used) / limit)) : 0;
  const tone: Tone = limit !== null && used >= limit ? "red" : pct >= 80 ? "amber" : "green";
  return (
    <View style={s.meter}>
      <Row spread>
        <T small muted>{caption}</T>
        <T small><T small bold>{used.toLocaleString()}</T> / {limit === null ? "unlimited" : limit.toLocaleString()}</T>
      </Row>
      <View style={[s.bar, { backgroundColor: c.track }]}>
        <View style={[s.barFill, { width: `${pct}%`, backgroundColor: c[tone] }]} />
      </View>
    </View>
  );
}

/** A row of a list that opens something. */
export function ListRow({ onPress, children }: { onPress?: () => void; children: ReactNode }) {
  const c = useColors();
  return (
    <Pressable
      accessibilityRole={onPress ? "button" : undefined}
      disabled={!onPress}
      onPress={onPress}
      style={({ pressed }) => [s.listRow, { backgroundColor: pressed ? c.panel2 : c.panel, borderColor: c.border }]}
    >
      {children}
    </Pressable>
  );
}

/** One message of a WhatsApp chat or a call transcript. */
export function Bubble({ mine, agent, text, meta }: { mine: boolean; agent?: boolean; text: string; meta: string }) {
  const c = useColors();
  return (
    <View
      style={[
        s.bubble, mine ? s.bubbleRight : s.bubbleLeft,
        { backgroundColor: !mine ? c.panel2 : agent ? c.greenSoft : c.primarySoft, borderColor: c.border },
      ]}
    >
      <T selectable>{text}</T>
      <T small muted style={s.bubbleMeta}>{meta}</T>
    </View>
  );
}

// ------------------------------------------------------------------ lists
/**
 * A list read page by page from one of the API's paged endpoints: pull down to refresh, scroll to
 * the end to load more. `header` (search box, filters) stays fixed above the list; `top` scrolls away
 * with it (a summary above the rows).
 */
export function PagedList<Item extends { id: string }>({ queryKey, url, params = {}, renderItem, header, top, empty, refetchInterval, keyOf }: {
  queryKey: unknown[];
  url: string;
  params?: Record<string, unknown>;
  renderItem: (item: Item) => ReactNode;
  header?: ReactNode;
  top?: ReactNode;
  empty: string;
  refetchInterval?: number;
  keyOf?: (item: Item) => string;
}) {
  const c = useColors();
  const q = useInfiniteQuery({
    queryKey: [...queryKey, params],
    initialPageParam: 1,
    queryFn: async ({ pageParam }) =>
      (await api.get<Page<Item>>(url, { params: cleanParams({ ...params, page: pageParam, size: 25 }) })).data,
    getNextPageParam: (last) => (last.page * last.size < last.total ? last.page + 1 : undefined),
    refetchInterval,
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <View style={[s.fill, { backgroundColor: c.bg }]}>
      {header && <View style={s.listHeader}>{header}</View>}
      <FlatList
        data={items}
        keyExtractor={keyOf ?? ((item) => item.id)}
        renderItem={({ item }) => <>{renderItem(item)}</>}
        contentContainerStyle={s.list}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={q.isRefetching && !q.isFetchingNextPage} onRefresh={() => q.refetch()} tintColor={c.primary} />}
        onEndReachedThreshold={0.4}
        onEndReached={() => q.hasNextPage && !q.isFetchingNextPage && q.fetchNextPage()}
        ListHeaderComponent={<View style={s.listTop}>{top}{q.error ? <ErrorBox error={q.error} /> : null}</View>}
        ListEmptyComponent={q.isLoading ? <ActivityIndicator style={s.spinner} color={c.primary} /> : q.error ? null : <Empty>{empty}</Empty>}
        ListFooterComponent={q.isFetchingNextPage ? <ActivityIndicator style={s.spinner} color={c.primary} /> : null}
      />
    </View>
  );
}

// ------------------------------------------------------------------ formatting
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  return m ? `${m}m ${seconds % 60}s` : `${seconds}s`;
}

export const fmtDate = (v: string | null | undefined) =>
  v ? new Date(v).toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) : "—";

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

const s = StyleSheet.create({
  fill: { flex: 1 },
  bold: { fontWeight: "700" },
  h2: { fontSize: 16, fontWeight: "700", marginBottom: 8 },
  page: { padding: 14, gap: 12, paddingBottom: 28 },
  card: { borderWidth: 1, borderRadius: 14, padding: 14, gap: 6 },
  row: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  spread: { justifyContent: "space-between" },
  kv: { flexDirection: "row", gap: 10, paddingVertical: 3 },
  kvKey: { width: 104, paddingTop: 2 },
  badge: { flexDirection: "row", alignItems: "center", gap: 5, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, alignSelf: "flex-start" },
  dot: { width: 6, height: 6, borderRadius: 3 },
  badgeText: { fontSize: 12, fontWeight: "600", textTransform: "capitalize" },
  avatar: { alignItems: "center", justifyContent: "center" },
  btn: { minHeight: 46, paddingHorizontal: 16, borderRadius: 10, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  btnSmall: { minHeight: 36, paddingHorizontal: 12 },
  btnText: { fontSize: 15, fontWeight: "700" },
  btnTextSmall: { fontSize: 13.5 },
  field: { gap: 5 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, minHeight: 46, fontSize: 15 },
  multiline: { minHeight: 84, paddingTop: 10, textAlignVertical: "top" },
  chips: { gap: 8, paddingRight: 14 },
  chip: { paddingHorizontal: 12, height: 34, borderRadius: 17, borderWidth: 1, justifyContent: "center" },
  note: { borderRadius: 10, padding: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  centerText: { textAlign: "center" },
  empty: { padding: 36, alignItems: "center" },
  stat: { flexGrow: 1, flexBasis: "45%", borderWidth: 1, borderRadius: 14, padding: 12, gap: 2 },
  statValue: { fontSize: 24, fontWeight: "800" },
  meter: { gap: 6, paddingVertical: 4 },
  bar: { height: 8, borderRadius: 4, overflow: "hidden" },
  barFill: { height: 8, borderRadius: 4 },
  listRow: { borderWidth: 1, borderRadius: 14, padding: 12, gap: 6 },
  listHeader: { paddingHorizontal: 14, paddingTop: 12, gap: 10 },
  list: { padding: 14, gap: 10, paddingBottom: 28 },
  listTop: { gap: 12 },
  spinner: { padding: 24 },
  bubble: { maxWidth: "86%", borderWidth: 1, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 8 },
  bubbleLeft: { alignSelf: "flex-start", borderBottomLeftRadius: 4 },
  bubbleRight: { alignSelf: "flex-end", borderBottomRightRadius: 4 },
  bubbleMeta: { marginTop: 3 },
});
