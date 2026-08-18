import { useEffect, useMemo, useState } from "react";
import HoloCard from "../../components/ui/HoloCard";
import HoloButton from "../../components/ui/HoloButton";
import HoloBadge from "../../components/ui/HoloBadge";
import { HoloInput, HoloSelect } from "../../components/ui/HoloInput";
import EmptyState from "../../components/common/EmptyState";
import { api } from "../../lib/api";
import type { Issue, IssueState } from "../../lib/types";
import { SEV_LABEL } from "../../lib/constants";
import { useToast } from "../../components/ui/Toast";

/* 标签3 · 错误详情：全部核查问题、筛选、标记复核状态 */
export default function IssuesTab() {
  const toast = useToast();
  const [issues, setIssues] = useState<Issue[]>([]);
  const [err, setErr] = useState("");
  const [sev, setSev] = useState("all");
  const [state, setState] = useState("all");
  const [q, setQ] = useState("");

  useEffect(() => {
    api
      .issues()
      .then((d) => setIssues(d.issues))
      .catch((e: Error) => setErr(e.message));
  }, []);

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    return issues.filter(
      (i) =>
        (sev === "all" || i.severity === sev) &&
        (state === "all" || i.state === state) &&
        (!kw || (i.rule_title + i.detail + i.location + i.file_name).toLowerCase().includes(kw)),
    );
  }, [issues, sev, state, q]);

  const toggle = async (i: Issue, act: "ignore" | "check") => {
    const next: IssueState =
      act === "ignore" ? (i.state === "ignored" ? "normal" : "ignored") : i.state === "checked" ? "normal" : "checked";
    try {
      await api.setIssueState({ file_index: i.file_index, issue_index: i.issue_index, state: next });
      setIssues((prev) => prev.map((x) => (x === i ? { ...x, state: next } : x)));
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  /* 人工确认内容正确 → 加入本地AI学习样本（仅用户主动标记，AI 不自动采集） */
  const markCorrect = async (i: Issue) => {
    const content = (i.snippet || i.detail || "").trim();
    if (!content) {
      toast("该问题无原文片段，无法作为学习样本", "warn");
      return;
    }
    try {
      const r = await api.aiMemoryAddSample({
        content,
        source_doc: i.file_name,
        note: i.location,
      });
      if (!r.ok) {
        toast(r.message || "加入学习库失败", "err");
        return;
      }
      toast("已确认正确并加入本地AI学习库，可在「规则与词库 → AI 规则词库智能生成 → 自学习」中补充修订表述后学习");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  if (err)
    return (
      <HoloCard className="p-6">
        <EmptyState text={`加载失败：${err}`} icon="⚠️" />
      </HoloCard>
    );

  return (
    <div className="space-y-4">
      {/* 筛选条 */}
      <div className="flex flex-wrap items-center gap-3">
        <HoloSelect className="w-[140px]" value={sev} onChange={(e) => setSev(e.target.value)}>
          <option value="all">全部级别</option>
          <option value="high">严重</option>
          <option value="medium">一般</option>
          <option value="low">轻微</option>
        </HoloSelect>
        <HoloSelect className="w-[150px]" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="all">全部状态</option>
          <option value="normal">待处理</option>
          <option value="ignored">已忽略</option>
          <option value="checked">已核查</option>
        </HoloSelect>
        <HoloInput
          className="min-w-[200px] flex-1"
          placeholder="搜索规则 / 原文 / 位置 / 文件名…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <span className="text-xs text-white/40">共 {issues.length} 条</span>
      </div>

      {/* 问题表 */}
      <HoloCard className="overflow-hidden p-0" glow="sm">
        <div className="overflow-x-auto">
          <table className="holo-table w-full text-[13.5px]">
            <thead>
              <tr className="text-xs text-white/60">
                <th className="px-4 py-3 text-left font-semibold">级别</th>
                <th className="px-4 py-3 text-left font-semibold">问题类型</th>
                <th className="px-4 py-3 text-left font-semibold">位置</th>
                <th className="px-4 py-3 text-left font-semibold">说明</th>
                <th className="px-4 py-3 text-left font-semibold">建议</th>
                <th className="px-4 py-3 text-left font-semibold">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length ? (
                filtered.map((i, idx) => (
                  <tr key={idx} className="border-b border-white/5 align-top text-white/85">
                    <td className="px-4 py-3">
                      <HoloBadge tone={i.severity as "sev-high" | "sev-medium" | "sev-low"}>
                        {SEV_LABEL[i.severity]}
                      </HoloBadge>
                    </td>
                    <td className="px-4 py-3">
                      <b>{i.rule_title}</b>
                      <div className="text-xs text-white/40">{i.file_name}</div>
                    </td>
                    <td className="px-4 py-3 text-sm text-white/70">{i.location}</td>
                    <td className="px-4 py-3 text-sm text-white/70">
                      {i.detail}
                      {i.snippet && <div className="mt-1 text-xs text-white/40">原文：{i.snippet}</div>}
                    </td>
                    <td className="px-4 py-3 text-sm text-white/50">{i.suggestion || "—"}</td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <div className="flex gap-2">
                        <HoloButton size="sm" onClick={() => toggle(i, "ignore")}>
                          {i.state === "ignored" ? "取消忽略" : "忽略"}
                        </HoloButton>
                        <HoloButton
                          size="sm"
                          variant={i.state === "checked" ? "primary" : "ghost"}
                          onClick={() => toggle(i, "check")}
                        >
                          {i.state === "checked" ? "取消核查" : "已核查"}
                        </HoloButton>
                        <HoloButton size="sm" variant="danger" onClick={() => markCorrect(i)} title="人工确认该内容正确，加入本地AI学习样本">
                          标记正确
                        </HoloButton>
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6}>
                    <EmptyState text="无匹配问题" icon="✨" />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </HoloCard>
    </div>
  );
}
