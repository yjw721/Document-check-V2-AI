import { Component, type ErrorInfo, type ReactNode } from "react";

interface BoundaryProps {
  children: ReactNode;
  /** 出错区域名称，用于提示定位（如「本地 AI 自学习」） */
  label?: string;
}

interface BoundaryState {
  error: Error | null;
}

/**
 * 页面级错误边界：任何子组件渲染崩溃时降级为错误卡片，而不是整站白屏。
 *
 * 背景：本次「本地 AI 自学习」页因后端字段与前端类型声明不一致
 * （revised 为对象却被当字符串渲染 → React #31）导致整页空白。
 * 加 ErrorBoundary 后同类问题只影响局部，并把真实错误信息显示出来便于定位。
 */
export default class ErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", this.props.label ?? "页面", error.message, info.componentStack?.slice(0, 400));
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="mx-auto max-w-[720px] rounded-2xl border border-[rgba(255,107,125,0.35)] bg-[rgba(255,107,125,0.08)] p-6">
        <h2 className="text-[15px] font-medium text-white">
          {this.props.label ? `「${this.props.label}」加载出错` : "页面加载出错"}
        </h2>
        <p className="mt-2 text-[13px] leading-relaxed text-white/70">
          界面渲染发生异常，其余功能不受影响。可尝试刷新页面；若反复出现，请把以下错误信息反馈给开发者。
        </p>
        <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-xl border border-white/10 bg-[rgba(10,10,31,0.6)] px-4 py-3 font-mono text-[12px] text-[var(--tone-danger-soft,rgba(255,107,125,0.9))]">
          {error.message}
        </pre>
        <button
          onClick={() => this.setState({ error: null })}
          className="mt-3 rounded-xl border border-white/15 bg-white/[0.06] px-4 py-2 text-[13px] text-white/85 transition-all duration-500 hover:border-[rgba(0,212,255,0.4)] hover:text-white active:scale-95"
        >
          重试渲染
        </button>
      </div>
    );
  }
}
