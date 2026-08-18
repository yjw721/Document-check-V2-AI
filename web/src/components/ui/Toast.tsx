import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

/* 全息 Toast：右下角堆叠，自动消失 */
type ToastKind = "ok" | "err" | "warn";

interface ToastItem {
  id: number;
  msg: string;
  kind: ToastKind;
}

interface ToastCtx {
  toast: (msg: string, kind?: ToastKind) => void;
}

const Ctx = createContext<ToastCtx>({ toast: () => {} });
export const useToast = () => useContext(Ctx).toast;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(1);

  const toast = useCallback((msg: string, kind: ToastKind = "ok") => {
    const id = idRef.current++;
    setItems((prev) => [...prev, { id, msg, kind }]);
    setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), 3200);
  }, []);

  return (
    <Ctx.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-6 right-6 z-[80] flex flex-col gap-2.5">
        {items.map((t) => (
          <div key={t.id} className={`holo-toast ${t.kind}`}>
            {t.msg}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
