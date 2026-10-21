import { useCallback, useMemo, useState } from "react";

function deepClone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

/* 
 * 统一的实体编辑器 Hook：复用 CustomRulesTab / WordbanksTab / AiCreateTab 的核心逻辑
 * - 分组/条目的增删改查
 * - 脏检测（对比原始快照）
 * - 折叠状态管理
 * - 批量启用/禁用
 * - 导入导出
 */
export interface EditorConfig<TGroup, TItem> {
  /** 从 API 加载数据 */
  load: () => Promise<{ groups: TGroup[] }>;
  /** 保存数据到 API */
  save: (data: { groups: TGroup[] }) => Promise<{ filtered_rules?: number; filtered_entries?: number; rejected?: any[] }>;
  /** 创建新分组 */
  createGroup: (name: string) => TGroup;
  /** 创建新条目 */
  createItem: () => TItem;
  /** 获取分组 ID */
  getGroupId: (group: TGroup) => string;
  /** 获取条目 ID */
  getItemId: (item: TItem) => string;
  /** 获取分组名称 */
  getGroupName: (group: TGroup) => string;
  /** 设置分组名称 */
  setGroupName: (group: TGroup, name: string) => TGroup;
  /** 获取分组条目列表 */
  getGroupItems: (group: TGroup) => TItem[];
  /** 设置分组条目列表 */
  setGroupItems: (group: TGroup, items: TItem[]) => TGroup;
  /** 获取条目名称/关键词 */
  getItemName: (item: TItem) => string;
  /** 设置条目启用状态 */
  setItemEnabled: (item: TItem, enabled: boolean) => TItem;
  /** 获取条目启用状态 */
  getItemEnabled: (item: TItem) => boolean;
  /** 获取分组启用状态 */
  getGroupEnabled: (group: TGroup) => boolean;
  /** 设置分组启用状态（级联条目） */
  setGroupEnabled: (group: TGroup, enabled: boolean) => TGroup;
  /** 比对两个分组差异生成日志详情 */
  diffGroup?: (oldGroup: TGroup, newGroup: TGroup) => string;
  /** 记录操作日志 */
  logActivity?: (detail: string) => Promise<void>;
  /** 发送变更事件 */
  emitChanged?: () => void;
}

export interface EditorState<TGroup> {
  data: { groups: TGroup[] } | null;
  saved: { groups: TGroup[] };
  folded: Set<string>;
  saving: boolean;
}

export function useEntityEditor<TGroup, TItem>(config: EditorConfig<TGroup, TItem>) {
  const [state, setState] = useState<EditorState<TGroup>>({
    data: null,
    saved: { groups: [] },
    folded: new Set(),
    saving: false,
  });

  // 加载数据
  const load = useCallback(async () => {
    try {
      const d = await config.load();
      const init = { groups: d.groups || [] };
      setState((s) => ({ ...s, data: deepClone(init), saved: deepClone(init) }));
    } catch (e) {
      console.error("加载失败:", e);
    }
  }, [config]);

  // 脏检测
  const { dirtySet, anyDirty } = useMemo(() => {
    if (!state.data) return { dirtySet: new Set<number>(), anyDirty: false };
    const savedById = new Map(state.saved.groups.map((g) => [config.getGroupId(g), g]));
    const dirty = new Set<number>();
    state.data.groups.forEach((g, i) => {
      const s = savedById.get(config.getGroupId(g));
      if (!s || JSON.stringify(g) !== JSON.stringify(s)) dirty.add(i);
    });
    const idsChanged =
      state.saved.groups.length !== state.data.groups.length ||
      state.saved.groups.some((g) => !state.data!.groups.find((x) => config.getGroupId(x) === config.getGroupId(g)));
    return { dirtySet: dirty, anyDirty: dirty.size > 0 || idsChanged };
  }, [state.data, state.saved, config]);

  // 更新分组
  const updateGroup = useCallback(
    (gi: number, patch: Partial<TGroup>) =>
      setState((s) =>
        s.data
          ? {
              ...s,
              data: {
                ...s.data,
                groups: s.data.groups.map((g, i) =>
                  i === gi ? { ...g, ...patch } : g
                ),
              },
            }
          : s
      ),
    [config]
  );

  // 更新条目
  const updateItem = useCallback(
    (gi: number, ii: number, patch: Partial<TItem>) =>
      setState((s) =>
        s.data
          ? {
              ...s,
              data: {
                ...s.data,
                groups: s.data.groups.map((g, i) =>
                  i === gi
                    ? config.setGroupItems(
                        g,
                        config.getGroupItems(g).map((it, j) => (j === ii ? { ...it, ...patch } : it))
                      )
                    : g
                ),
              },
            }
          : s
      ),
    [config]
  );

  // 添加分组
  const addGroup = useCallback(() =>
    setState((s) =>
      s.data
        ? {
            ...s,
            data: {
              ...s.data,
              groups: [...s.data.groups, config.createGroup("新分组")],
            },
          }
        : s
    ),
    [config]
  );

  // 删除分组
  const deleteGroup = useCallback((gi: number) =>
    setState((s) =>
      s.data
        ? {
            ...s,
            data: { ...s.data, groups: s.data.groups.filter((_, i) => i !== gi) },
          }
        : s
    ),
    [config]
  );

  // 添加条目
  const addItem = useCallback((gi: number) =>
    setState((s) =>
      s.data
        ? {
            ...s,
            data: {
              ...s.data,
              groups: s.data.groups.map((g, i) =>
                i === gi ? config.setGroupItems(g, [...config.getGroupItems(g), config.createItem()]) : g
              ),
            },
          }
        : s
    ),
    [config]
  );

  // 删除条目
  const deleteItem = useCallback((gi: number, ii: number) =>
    setState((s) =>
      s.data
        ? {
            ...s,
            data: {
              ...s.data,
              groups: s.data.groups.map((g, i) =>
                i === gi ? config.setGroupItems(g, config.getGroupItems(g).filter((_, j) => j !== ii)) : g
              ),
            },
          }
        : s
    ),
    [config]
  );

  // 切换折叠
  const toggleFold = useCallback((id: string) =>
    setState((s) => {
      const n = new Set(s.folded);
      n.has(id) ? n.delete(id) : n.add(id);
      return { ...s, folded: n };
    }),
    []
  );

  // 批量启用/禁用分组
  const setGroupEnabled = useCallback((gi: number, enabled: boolean) =>
    setState((s) =>
      s.data
        ? {
            ...s,
            data: {
              ...s.data,
              groups: s.data.groups.map((g, i) =>
                i === gi ? config.setGroupEnabled(g, enabled) : g
              ),
            },
          }
        : s
    ),
    [config]
  );

  // 保存单分组
  const saveGroup = useCallback(async (gi: number) => {
    if (!state.data) return;
    const cur = state.data;
    const group = cur.groups[gi];
    if (config.diffGroup) {
      const savedGroup = state.saved.groups.find((g) => config.getGroupId(g) === config.getGroupId(group));
      if (savedGroup) {
        const detail = config.diffGroup(savedGroup, group);
        await config.logActivity?.(detail);
      }
    }
    setState((s) => ({ ...s, saving: true }));
    try {
      await config.save(cur);
      setState((s) => ({ ...s, saved: deepClone(cur), saving: false }));
      config.emitChanged?.();
      return true;
    } catch (e) {
      setState((s) => ({ ...s, saving: false }));
      throw e;
    }
  }, [state.data, state.saved, config]);

  // 保存全部
  const saveAll = useCallback(async () => {
    if (!state.data) return;
    setState((s) => ({ ...s, saving: true }));
    try {
      await config.save(state.data);
      setState((s) => ({ ...s, saved: deepClone(state.data!), saving: false }));
      config.emitChanged?.();
      return true;
    } catch (e) {
      setState((s) => ({ ...s, saving: false }));
      throw e;
    }
  }, [state.data, config]);

  // 重置分组
  const resetGroup = useCallback((gi: number) => {
    if (!state.data) return;
    const group = state.data.groups[gi];
    const savedGroup = state.saved.groups.find((g) => config.getGroupId(g) === config.getGroupId(group));
    if (savedGroup) {
      setState((s) =>
        s.data
          ? {
              ...s,
              data: {
                ...s.data,
                groups: s.data.groups.map((g, i) =>
                  i === gi ? deepClone(savedGroup) : g
                ),
              },
            }
          : s
      );
    }
  }, [state.data, state.saved, config]);

  // 导出 JSON
  const exportJson = useCallback(() => {
    if (!state.data?.groups.length) return;
    const blob = new Blob([JSON.stringify(state.data, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `导出_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, [state.data]);

  return {
    ...state,
    load,
    dirtySet,
    anyDirty,
    updateGroup,
    updateItem,
    addGroup,
    deleteGroup,
    addItem,
    deleteItem,
    toggleFold,
    setGroupEnabled,
    saveGroup,
    saveAll,
    resetGroup,
    exportJson,
  };
}