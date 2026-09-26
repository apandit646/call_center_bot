import { useEffect, useState } from "react";
import { IconMonitor, IconMoon, IconSun } from "./components/Icons";

export type ThemeChoice = "light" | "dark" | "system";
const KEY = "theme";
const media = () => window.matchMedia("(prefers-color-scheme: dark)");

function stored(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" || v === "system" ? v : "system";
  } catch {
    return "system";
  }
}

/** Resolve the choice to light/dark and put it on <html data-theme>, which the CSS tokens key off. */
export function applyTheme(choice: ThemeChoice = stored()) {
  const dark = choice === "dark" || (choice === "system" && media().matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function useTheme(): [ThemeChoice, (c: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(stored);

  useEffect(() => {
    applyTheme(choice);
    if (choice !== "system") return;
    const mq = media();
    const onChange = () => applyTheme("system"); // follow the OS live while on "system"
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [choice]);

  const set = (c: ThemeChoice) => {
    try {
      localStorage.setItem(KEY, c);
    } catch {
      /* storage blocked: still apply for this session */
    }
    setChoice(c);
  };
  return [choice, set];
}

export function ThemeSwitch({ onLight = false }: { onLight?: boolean }) {
  const [choice, setChoice] = useTheme();
  const options: { value: ThemeChoice; label: string; icon: JSX.Element }[] = [
    { value: "light", label: "Light", icon: <IconSun size={14} /> },
    { value: "dark", label: "Dark", icon: <IconMoon size={14} /> },
    { value: "system", label: "Auto", icon: <IconMonitor size={14} /> },
  ];
  return (
    <div className={`theme-switch ${onLight ? "light-surface" : ""}`} role="radiogroup" aria-label="Theme">
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={choice === o.value}
          className={choice === o.value ? "active" : ""} onClick={() => setChoice(o.value)} title={`${o.label} theme`}>
          {o.icon} {o.label}
        </button>
      ))}
    </div>
  );
}
