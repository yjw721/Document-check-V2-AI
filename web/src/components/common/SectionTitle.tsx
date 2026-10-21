import type { ReactNode } from "react";

/* 区块标题：渐变竖条 + 标题 + 可选右侧插槽 */
export default function SectionTitle({
  children,
  extra,
  className = "",
}: {
  children: ReactNode;
  extra?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mb-4 flex items-center gap-2.5 ${className}`}>
      <span className="holo-accent-bar h-4 w-1 rounded" />
      <h3 className="text-[15px] font-bold text-white">{children}</h3>
      {extra && <div className="ml-auto">{extra}</div>}
    </div>
  );
}
