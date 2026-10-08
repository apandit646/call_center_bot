import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

/** What the "are you sure?" dialog says. */
export interface Ask {
  title: string; // the question, e.g. "Delete this campaign?"
  message?: ReactNode; // what happens if they go ahead
  confirm?: string; // the button that goes ahead, e.g. "Delete campaign"
  cancel?: string; // the button that backs out
  danger?: boolean; // red button: the action removes or stops something
}

const Context = createContext<(ask: Ask) => Promise<boolean>>(async () => false);

/** The app's own "are you sure?" dialog, instead of the browser's plain confirm box:
 *     const ask = useConfirm();
 *     if (await ask({ title: "Delete it?", confirm: "Delete", danger: true })) remove();
 */
export function useConfirm() {
  return useContext(Context);
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<(Ask & { done: (yes: boolean) => void }) | null>(null);
  const ask = useCallback((a: Ask) => new Promise<boolean>((done) => setOpen({ ...a, done })), []);
  const close = (yes: boolean) => {
    open?.done(yes);
    setOpen(null);
  };
  return (
    <Context.Provider value={ask}>
      {children}
      {open && <Dialog ask={open} onClose={close} />}
    </Context.Provider>
  );
}

function Dialog({ ask, onClose }: { ask: Ask; onClose: (yes: boolean) => void }) {
  const back = useRef<HTMLButtonElement>(null);
  const before = useRef<Element | null>(null);
  useEffect(() => {
    // Focus the safe choice, close on Escape, and give focus back to the button that opened it.
    before.current = document.activeElement;
    back.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(false); };
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      (before.current as HTMLElement | null)?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={() => onClose(false)}>
      <div className="modal confirm" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby={ask.message ? "confirm-message" : undefined}
        onClick={(e) => e.stopPropagation()}>
        <h2 id="confirm-title">{ask.title}</h2>
        {ask.message && <p id="confirm-message" className="muted">{ask.message}</p>}
        <div className="confirm-actions">
          <button ref={back} className="btn ghost" onClick={() => onClose(false)}>{ask.cancel ?? "Go back"}</button>
          <button className={`btn ${ask.danger ? "danger" : ""}`} onClick={() => onClose(true)}>{ask.confirm ?? "Yes, continue"}</button>
        </div>
      </div>
    </div>
  );
}
