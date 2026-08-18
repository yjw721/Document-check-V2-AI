interface HoloSwitchProps {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label?: string;
}

/* 全息开关：选中时渐变流光 */
export default function HoloSwitch({ checked, onChange, disabled, label }: HoloSwitchProps) {
  return (
    <label
      className={`inline-flex items-center gap-2.5 select-none ${disabled ? "opacity-50" : "cursor-pointer"}`}
    >
      <span
        className={`relative inline-block h-6 w-11 rounded-full border transition-all duration-500 ${
          checked
            ? "border-transparent holo-bg shadow-[0_0_15px_var(--glow-btn)]"
            : "border-white/10 bg-white/10"
        }`}
      >
        <input
          type="checkbox"
          className="peer sr-only"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span
          className={`absolute top-[3px] h-[18px] w-[18px] rounded-full bg-white transition-all duration-500 ${
            checked ? "left-[22px]" : "left-[3px]"
          }`}
        />
      </span>
      {label && <span className="text-sm text-white/70">{label}</span>}
    </label>
  );
}
