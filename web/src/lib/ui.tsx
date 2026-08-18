import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import Overlay from "../components/ui/Overlay";

/* 全局加载遮罩上下文 */
interface OverlayCtx {
  show: (text?: string) => void;
  hide: () => void;
}

const Ctx = createContext<OverlayCtx>({ show: () => {}, hide: () => {} });
export const useOverlay = () => useContext(Ctx);

export function OverlayProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ show: boolean; text: string }>({ show: false, text: "" });

  const show = useCallback((text = "处理中…") => setState({ show: true, text }), []);
  const hide = useCallback(() => setState((s) => ({ ...s, show: false })), []);

  return (
    <Ctx.Provider value={{ show, hide }}>
      {children}
      <Overlay show={state.show} text={state.text} />
    </Ctx.Provider>
  );
}
