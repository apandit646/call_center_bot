import { useColorScheme } from "react-native";

// The same colours as the web portal (frontend/src/index.css), light and dark.
const light = {
  bg: "#f4f5fa", panel: "#ffffff", panel2: "#f8f9fc", text: "#0f1222", text2: "#3a4153", muted: "#6b7285",
  border: "#e6e8f0", track: "#eceef5", inputBg: "#ffffff",
  primary: "#5b5bf0", primaryText: "#4f46e5", primarySoft: "#eef0ff", onPrimary: "#ffffff",
  green: "#059669", greenSoft: "#e6f6ef", amber: "#b45309", amberSoft: "#fef3df", red: "#dc2626", redSoft: "#fdecec",
  blue: "#2563eb", blueSoft: "#e8effe", violet: "#7c3aed", violetSoft: "#f1eafe", grey: "#4b5563", greySoft: "#eef0f4",
};

const dark: typeof light = {
  bg: "#090b16", panel: "#111427", panel2: "#161a31", text: "#eceef8", text2: "#c5cadd", muted: "#8a91ab",
  border: "#222844", track: "#1f2440", inputBg: "#0d1021",
  primary: "#6d6cf7", primaryText: "#a5b4fc", primarySoft: "#1c2046", onPrimary: "#ffffff",
  green: "#34d399", greenSoft: "#0f2a23", amber: "#fbbf24", amberSoft: "#2e2410", red: "#f87171", redSoft: "#331616",
  blue: "#60a5fa", blueSoft: "#13213f", violet: "#a78bfa", violetSoft: "#231a40", grey: "#a3abbf", greySoft: "#1b2036",
};

export type Colors = typeof light;

/** Follows the phone's light / dark setting. */
export function useColors(): Colors {
  return useColorScheme() === "dark" ? dark : light;
}
