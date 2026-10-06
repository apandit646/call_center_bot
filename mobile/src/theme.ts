import { useColorScheme } from "react-native";

// The same "help desk" look as the web portal (frontend/src/index.css): a soft grey page, white
// rounded cards, near-black (ink) pill buttons and one violet accent. Light and dark.
const light = {
  bg: "#eef0f4", panel: "#ffffff", panel2: "#f4f5f8", text: "#0b0d12", text2: "#3b4250", muted: "#5a6170",
  border: "#f0f1f4", borderStrong: "#d5d9e0", track: "#e6e8ee", inputBg: "#ffffff",
  ink: "#0b0d12", onInk: "#ffffff", // buttons, the selected chip and tab
  primary: "#5d4df5", primaryText: "#3a2cc4", primarySoft: "#ecebfe", onPrimary: "#ffffff",
  green: "#0a5c3f", greenSoft: "#dcf3e9", amber: "#6b3500", amberSoft: "#fff1db", red: "#9b1c1c", redSoft: "#fde4e2",
  blue: "#3a2cc4", blueSoft: "#ecebfe", violet: "#6b21a8", violetSoft: "#f3e8ff", grey: "#3b4250", greySoft: "#f0f1f4",
};

const dark: typeof light = {
  bg: "#0b0d12", panel: "#15181f", panel2: "#1e222b", text: "#f2f3f6", text2: "#c3c8d2", muted: "#9aa1ae",
  border: "#232732", borderStrong: "#363c49", track: "#1e222b", inputBg: "#1e222b",
  ink: "#ffffff", onInk: "#0b0d12",
  primary: "#7b6ef7", primaryText: "#b4adfb", primarySoft: "#24214a", onPrimary: "#ffffff",
  green: "#6ee7b7", greenSoft: "#12291f", amber: "#ffc857", amberSoft: "#2e2410", red: "#fca5a5", redSoft: "#331616",
  blue: "#b4adfb", blueSoft: "#24214a", violet: "#d8b4fe", violetSoft: "#2a1c40", grey: "#c3c8d2", greySoft: "#1e222b",
};

export type Colors = typeof light;

/** Follows the phone's light / dark setting. */
export function useColors(): Colors {
  return useColorScheme() === "dark" ? dark : light;
}
