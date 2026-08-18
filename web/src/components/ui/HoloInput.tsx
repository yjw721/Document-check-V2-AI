import type { InputHTMLAttributes, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

/* 全息输入框：backdrop-blur + bg-white/5 + rounded-2xl + focus:border-purple + focus 光晕 */

const FIELD_BASE =
  "w-full rounded-2xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm text-white placeholder:text-white/55 " +
  "backdrop-blur-xl transition-all duration-500 outline-none " +
  "hover:border-white/20 " +
  "focus:border-[var(--border-accent)] focus:shadow-[0_0_20px_var(--glow-input)] focus:bg-white/[0.08]";

export function HoloInput({ className = "", ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${FIELD_BASE} ${className}`} {...rest} />;
}

export function HoloSelect({ className = "", children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={`${FIELD_BASE} cursor-pointer ${className}`} {...rest}>
      {children}
    </select>
  );
}

export function HoloTextarea({ className = "", ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${FIELD_BASE} min-h-[90px] leading-relaxed resize-y ${className}`} {...rest} />;
}

export function FieldLabel({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <label className={`mb-2 block text-xs text-white/70 ${className}`}>{children}</label>;
}
