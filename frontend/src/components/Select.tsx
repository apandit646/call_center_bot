import {
  Children, isValidElement, useEffect, useId, useLayoutEffect, useRef, useState,
  type CSSProperties, type KeyboardEvent, type ReactElement, type ReactNode,
} from "react";
import { createPortal } from "react-dom";

interface Option { value: string; label: string }

/** The same props as the <select> it replaces, so swapping one in changes nothing else: options are
 *  still <option value="...">Label</option> children, and onChange still gets `e.target.value`. */
interface Props {
  value: string;
  onChange?: (e: { target: { value: string } }) => void;
  children: ReactNode;
  disabled?: boolean;
  required?: boolean;
  className?: string;
  style?: CSSProperties;
  id?: string;
  "aria-label"?: string;
}

const text = (node: ReactNode): string =>
  Children.toArray(node).map((n) => (typeof n === "string" || typeof n === "number" ? String(n) : isValidElement(n) ? text((n.props as { children?: ReactNode }).children) : "")).join("");

function optionsOf(children: ReactNode): Option[] {
  const out: Option[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    const el = child as ReactElement<{ value?: string | number; children?: ReactNode }>;
    if (el.type === "option") {
      const label = text(el.props.children);
      out.push({ value: el.props.value === undefined ? label : String(el.props.value), label });
    } else {
      out.push(...optionsOf(el.props.children)); // a fragment around some options
    }
  });
  return out;
}

/** A dropdown in the app's own style (the browser's own list cannot be styled). Keyboard: arrows, Home/End,
 *  Enter or Space to pick, Escape to close, and typing the first letters jumps to an option. */
export function Select({ value, onChange, children, disabled, required, className, style, id, "aria-label": ariaLabel }: Props) {
  const options = optionsOf(children);
  const selected = options.findIndex((o) => o.value === value);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [place, setPlace] = useState<{ left: number; width: number; top?: number; bottom?: number; maxHeight: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const typed = useRef({ text: "", at: 0 });
  const listId = useId();

  const show = () => {
    if (disabled || !options.length) return;
    setActive(Math.max(0, selected));
    setOpen(true);
  };
  const pick = (i: number) => {
    const option = options[i];
    setOpen(false);
    button.current?.focus();
    if (option && option.value !== value) onChange?.({ target: { value: option.value } });
  };

  // Below the button, or above it when there is more room there. Fixed to the window, so a table or
  // a card that cuts off what overflows never hides the list.
  useLayoutEffect(() => {
    if (!open || !button.current) return;
    const r = button.current.getBoundingClientRect();
    const below = window.innerHeight - r.bottom - 12, above = r.top - 12;
    const up = below < 220 && above > below;
    setPlace({
      left: r.left, width: Math.max(r.width, 180),
      ...(up ? { bottom: window.innerHeight - r.top + 6 } : { top: r.bottom + 6 }),
      maxHeight: Math.min(320, up ? above : below),
    });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      const t = e.target as Node;
      if (!list.current?.contains(t) && !button.current?.contains(t)) setOpen(false);
    };
    const moved = (e: Event) => { if (!list.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", away);
    window.addEventListener("scroll", moved, true);
    window.addEventListener("resize", moved);
    return () => {
      document.removeEventListener("pointerdown", away);
      window.removeEventListener("scroll", moved, true);
      window.removeEventListener("resize", moved);
    };
  }, [open]);

  useEffect(() => {
    if (open && place) list.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active, place]);

  const key = (e: KeyboardEvent) => {
    const last = options.length - 1;
    const move = (i: number) => { e.preventDefault(); if (open) setActive(Math.min(last, Math.max(0, i))); else show(); };
    switch (e.key) {
      case "ArrowDown": return move(active + 1);
      case "ArrowUp": return move(active - 1);
      case "Home": return open && move(0);
      case "End": return open && move(last);
      case "Enter": case " ":
        e.preventDefault();
        return open ? pick(active) : show();
      case "Escape": case "Tab":
        if (open) { if (e.key === "Escape") e.preventDefault(); setOpen(false); }
        return;
      default:
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
          const now = Date.now();
          typed.current = { text: (now - typed.current.at < 700 ? typed.current.text : "") + e.key.toLowerCase(), at: now };
          const from = open ? active : Math.max(0, selected);
          const order = [...options.keys()].map((k) => (k + from + (typed.current.text.length === 1 ? 1 : 0)) % options.length);
          const hit = order.find((i) => options[i].label.toLowerCase().startsWith(typed.current.text));
          if (hit !== undefined) { if (open) setActive(hit); else pick(hit); }
        }
    }
  };

  return (
    <span className={`select ${className ?? ""}`} style={style}>
      <button ref={button} type="button" id={id} className="select-btn" disabled={disabled} aria-label={ariaLabel}
        aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? `${listId}-${active}` : undefined}
        onClick={() => (open ? setOpen(false) : show())} onKeyDown={key}>
        <span className="select-value">{options[selected]?.label ?? "Choose…"}</span>
        <svg className="select-chevron" width="16" height="16" viewBox="0 0 24 24" aria-hidden><path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      {/* Lets a form refuse to submit while nothing is chosen, like a required <select>. */}
      {required && <input className="select-required" tabIndex={-1} aria-hidden value={selected >= 0 ? value : ""} required onChange={() => {}} />}
      {open && place && createPortal(
        <ul ref={list} id={listId} role="listbox" className="select-list" aria-label={ariaLabel}
          style={{ left: place.left, width: place.width, top: place.top, bottom: place.bottom, maxHeight: place.maxHeight }}>
          {options.map((o, i) => (
            <li key={`${o.value}-${i}`} id={`${listId}-${i}`} data-i={i} role="option" aria-selected={i === selected}
              className={`${i === active ? "active" : ""} ${i === selected ? "selected" : ""}`}
              onPointerMove={() => setActive(i)} onClick={() => pick(i)}>
              <span>{o.label}</span>
              {i === selected && <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden><path d="M20 6 9 17l-5-5" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" /></svg>}
            </li>
          ))}
        </ul>,
        document.body,
      )}
    </span>
  );
}
