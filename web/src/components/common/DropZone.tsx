import { forwardRef, useImperativeHandle, useRef, useState, type ChangeEvent, type DragEvent, type ReactNode } from "react";

/* 全息拖放区：拖拽/点击选择，支持多文件；暴露 pick() 供外部按钮触发 */
export interface DropZoneHandle {
  pick: () => void;
}

interface DropZoneProps {
  onFiles: (files: File[]) => void;
  children: ReactNode;
  accept?: string;
  disabled?: boolean;
}

const DropZone = forwardRef<DropZoneHandle, DropZoneProps>(function DropZone(
  { onFiles, children, accept, disabled },
  ref,
) {
  const [over, setOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useImperativeHandle(ref, () => ({ pick: () => inputRef.current?.click() }));

  const handle = (list: FileList | null) => {
    if (!list) return;
    onFiles(Array.from(list));
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setOver(false);
    if (!disabled) handle(e.dataTransfer.files);
  };

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    handle(e.target.files);
    e.target.value = "";
  };

  return (
    <div
      className={`holo-drop ${over ? "over" : ""} ${disabled ? "pointer-events-none opacity-50" : ""}`}
      onClick={(e) => {
        const t = e.target as HTMLElement;
        if (t.closest("a,button,input")) return;
        inputRef.current?.click();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      {children}
      <input ref={inputRef} type="file" multiple accept={accept} className="hidden" onChange={onChange} />
    </div>
  );
});

export default DropZone;
