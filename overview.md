# 词库与标准规则批量导入模块 · 交付总览

**日期**：2026-08-14 · **项目**：文档核验中心（离线保密版 v2.0，FastAPI + 单文件 SPA）

## 本次完成内容

1. **模块升级**：原「范本导入生成规则」→ **「词库与标准规则导入」**（`static/index.html`，导航更名，📥 图标，位于词库管理与报告导出之间）
   - 上传区新增 **7 类基准文件类别** 单选（通用检测规则库 / 行业规范词库 / 资产评估准则 / 执业规范模板 / 标准纠错库 / 禁用词库 / 公文规范库），类别决定导入分组名（`外部导入 · 类别名`）与无标签信息时的默认分级
   - 兼容 6 种格式：**.docx / .pdf / .txt / .csv / .scel（搜狗词库）/ 自定义正则规则文本 / 行业纠错文本库**（TXT 一行式，自动识别纠错对 / 正则 / 词条）
   - 基准文件卡片按格式显示徽标（📘Word 📕PDF 📄TXT 📊CSV 🔤SCEL）与解析量（文本块·页数 / 行·词条数）
   - 文本基准无页码，来源列显示「（文本基准）」；文档基准保留页码定位
   - 50MB 上限，仅本地内存 BytesIO 处理，零联网、零上传

2. **解析引擎扩展**（`checkers/template_parser.py`，契约不变：docs/rules/entries/conflicts/references）
   - **TXT / 行业纠错文本库**：逐行启发式——`错误=>正确` 纠错对 → 笔误警示词条；正则特征行（`/pattern/` 或 `pattern::建议`）→ 正则规则；`错误,正确[,建议]` → 纠错词条；普通行 → 词条；`#//;` 注释行跳过
   - **CSV**：表头列名自适应（错误词/正确词/正则/风险级别/整改建议 等关键词映射列角色），带 BOM（utf-8-sig）兼容；无表头按列数猜测
   - **SCEL 搜狗词库**：二进制解析（0x3B60 词条区 + UTF-16LE 词条 + 扩展区跳过），词条直接入库
   - **标签启发式**：绝对化/承诺用语 → 执业风险警示；口语化 → 表述优化建议；正则规则默认格式严重错误；禁用词库默认执业风险警示、公文规范库默认格式严重错误
   - 文档类（docx/pdf）原有提取链路、页码定位、术语冲突、参考句全部保留

3. **API**：`POST /api/template/upload` 新增 `category` 表单字段，格式白名单扩展 `.txt/.csv/.scel`；draft/select/import/clear 不变

4. **端到端验证**（测试脚本留存可复跑：`tests/_tpl_e2e2.py`）
   - 混合上传 TXT+CSV+SCEL（category=标准纠错库）→ 3 文件全部解析；正则规则 1 条、词条 11 条，标签映射正确（纠错对→笔误警示、CSV 风险级别列→执业风险警示、绝对保证→执业风险警示）
   - 导入 → 追加 `外部导入 · 标准纠错库` 组到 custom_rules / wordbanks，原配置不受影响
   - 违规文档检测：导入词条命中携带 **位置 + 标签**（第 1 页（估算）· 第 3 段 / 执业风险警示）
   - docx 范本回归不受影响（页码定位保留）；测试数据已清理，服务冒烟通过
   - 共 **23/23 断言通过**

## 关键决策
- 扩展而非重写：沿用既有 draft/select/import/clear 契约与 CustomRuleEngine/WordBankEngine 通道，导入后自动适配检测、页码定位、前端展示、Word 报告导出，检测器与报告零改动
- 文本类文件来源显示「（文本基准）」而非伪页码，保证定位信息真实可信

## 后续可选项
- SCEL 支持拼音/词频字段入库（当前仅取词条正文）
- 可增加「规则表达式合法性预检」高亮非法正则

## DCS 变体 SCEL 修复（2026-08-14 追加）

**问题**：导入真实文件「财务会计 财会词汇大全【官方推荐】.scel」（280,780 B）提示「解析错误」。

**根因**：该文件是 **DCS 变体**搜狗词库（第三方导出/转换工具产物，魔数 `DCS`），
标准解析器假设的 0x40 区段偏移全为 0，按标准 SCEL 定位词条区必然失败 → 返回空 → 报「解析错误」。

**修复**（`checkers/template_parser.py`，标准 SCEL 分支零改动）：
1. `parse_scel_bytes` 按 `raw[4:7]==b"DCS"` 分发到 `_parse_dcs_variant`；
2. 双类型记录解析：完整（flag∈1..4 + plen 偶数 + 拼音区）/ 精简（复用拼音，首 u16 即词长）；
   词频 2 字节（标准为 4），零填充 4/6/8B 可变；词首 Unicode ≥0x4E00 保证无回溯判别；
3. 词条流为 0x2628→EOF 单条连续流，起点动态定位（0x100 起 2B 步长，取首个连续解析满 50 条者）；
4. `_plausible_scel_word`（CJK/字母数字占比 ≥60%）过滤误判；
5. **草案上限**：`_add_text_entry` 增 `cap` 参数，SCEL 词库来源 cap=10000（纯词条集合，避免 300 截断），
   规则/纠错文本来源仍 300。

**验证**：真实文件完整解析 **7,631 条**（0.12s，unique 7,631）；E2E 上传 ok=True、草案 7,631 条、
导入 7,631 条；抽查 10 术语全命中；回归 `tests/_tpl_e2e2.py` **21/21 通过**；测试导入数据已清理。

**说明**：词库中不含 `资产负债表/增值税/主营业务收入/坏账准备/所有者权益` 等词为词库本身缺词，
`营业收入` 仅存在于长词（如「其它营业收入」）子串，均非解析遗漏。

---

## 规则与词库统一管理模块整合（UI 架构调整）

**日期**：2026-08-14 · **范围**：纯前端重构（`static/index.html`），后端 API / 检测逻辑零改动

### 改动
1. **侧边栏收敛**：移除分散的「自定义规则、词库管理、词库与标准规则导入」三个菜单，合并为单一入口【规则与词库统一管理】（`🗄️` 图标，位于规则词库与报告导出之间）；导航由 11 项精简到 9 项。
2. **顶部标签页切换**（`viewUnified` + `paintUni`）：
   - 标签 1 · 自定义正则规则（`viewCustomRules`）
   - 标签 2 · 自定义词库（`viewWordbanks`）
   - 标签 3 · 词库与标准规则批量导入（`viewTemplate`）
   - 激活态高亮、`uniFade` 进场动画；hash 子路由 `#unified/<标签>`，旧 hash `#customRules/#wordbanks/#template` 自动映射到对应标签（`history.replaceState` 兜底 `location.hash`）。
3. **三个原视图改造**：签名增 `root` 参数，渲染写入对应 `#uni-<tab>` 面板而非 `#view`；标签 3 的解析/导入/清空回调改为 `paintUni()` 原地刷新，不再 `route()` 跳转。
4. **新增交互**：
   - 标签 1 新增「⬇ 批量导出规则」按钮（导出完整 JSON，可回导）；
   - 标签 1 / 2 每个分组新增「折叠 / 展开」按钮（`.group-box.folded .subtbl{display:none}`）与「☑ 启用本组 / 停用本组」批量勾选。
5. **功能逻辑不变**：导入生成的规则 / 词条仍追加到 `custom_rules.json` / `wordbanks.json`，标签 1 / 2 进入即重新 GET 可见；校验、页码定位、报告导出链路不动。

### 验证
- JS 语法校验通过（`node --check`）；
- **jsdom DOM 冒烟 27/27 通过**（`tests/_uni_dom_smoke.js`）：导航收敛、旧 hash 映射、三标签切换与高亮、hash 同步、折叠/展开、批量启停 DOM 断言；
- 后端回归 `tests/_tpl_e2e2.py` **23/23 通过**（自定义词条命中、页码定位、docx 范本回归、检测适配无回归）。

---

## 检测管理模块整合（UI 架构调整）

**日期**：2026-08-14 · **范围**：纯前端重构（`static/index.html`），后端 API / 检测逻辑零改动

### 改动
1. **侧边栏最终精简为 5 项**：总览面板、检测管理、规则与词库统一管理、后台设置、缓存与系统。移除分散的「导入与检测、文件列表、问题清单、规则词库、报告导出」五个菜单，导航由 9 项精简到 5 项。
2. **检测管理页面**（`viewDetection` + `paintDetection`，复用统一管理的标签页框架）：
   - 标签 1 · 导入与检测（`viewUpload`）— 文件上传、批量启动检测、文件夹扫描
   - 标签 2 · 文件列表（`viewFiles`）— 已导入文档清单、清空检测数据
   - 标签 3 · 错误详情（`viewIssues`）— 全部核查问题、筛选、标记复核状态
   - 标签 4 · 报告导出（`viewReport`）— 检测报告配置、生成并下载 Word 核查报告
   - 激活态高亮、`uniFade` 进场动画；hash 子路由 `#detection/<标签>`，旧 hash `#upload/#files/#issues/#report` 自动映射；旧 `#rules` 重定向到 `#unified/customRules`。
3. **四个原视图改造**：签名增 `root` 参数，渲染写入对应 `#det-<tab>` 面板而非 `#view`；`setView("files")` 改为 `location.hash="#detection/files"`；概览页与文件列表的导航按钮增 `data-det` 属性直达子标签。
4. **跨标签数据联动**：每次切换标签 `paintDetection` 调用对应视图函数重新 GET 最新数据，检测产生的问题实时同步至错误详情标签。
5. **功能逻辑不变**：文件解析、错误定位（Word/PDF 页码、Excel 行列）、筛选检索、导出模板全部维持不变。

### 验证
- JS 语法校验通过（`node --check`）；
- **jsdom DOM 冒烟 32/32 通过**（`tests/_det_dom_smoke.js`）：NAV 精简 5 项、旧 hash 映射（upload/files/issues/report/rules）、四标签切换与高亮、面板内容渲染、概览页按钮指向；
- 后端回归 `tests/_tpl_e2e2.py` **23/23 通过**（检测链路无回归）。

---

## 全息 React UI · 规则与词库统一管理页补齐（web/ 新前端）

**日期**：2026-08-14 · **范围**：`web/src/pages/unified/` 三个标签组件全部完成

### 改动
1. **WordbanksTab.tsx**（标签 2 · 自定义词库）：
   - 分组管理（新建/删除/折叠）、词条行内编辑（关键词/标签/建议）、单条与整组启用/停用、保存；
   - 批量导入弹窗：后端 `/api/wordbanks/import` **仅解析文本不落盘**，前端将返回词条并入新分组「批量导入 · 日期」后整体保存（修正了「解析后直接保存旧数据导致导入丢失」的隐患）。
2. **TemplateImportTab.tsx**（标签 3 · 词库与标准规则批量导入）：
   - DropZone 上传 `.docx/.pdf/.txt/.csv/.scel`（多选、≤50MB）+ 7 类基准类别选择；
   - 草案四段展示：已解析文件卡片、规则表（勾选/全选/类型/匹配式/标签徽章/建议/来源·页码）、词条表、矛盾冲突（琥珀警示卡）、参考资料（可滚动引文列表）；
   - 勾选即同步 `POST /api/template/select`，确认导入 `templateImport`（仅追加不覆盖）、清空草案 `templateClear`。
3. **共享缺陷修复（根因）**：`Toast.tsx` 的 `useToast` 原返回上下文对象而非函数，导致全站 `ToastCtx 不可调用`（含新组件）——改为 `useContext(Ctx).toast` 一处修复全部页面；另清理 TabBar / ReportTab / CustomRulesTab 未用导入、UploadTab 的 HoloInput 默认导入改具名导入、`types.ts` RulesData 索引签名放宽（meta/global 与 RuleDef 并存）。

### 验证
- `tsc --noEmit` 全绿（唯一剩余报错：`App.tsx` 引用尚未创建的 `AdminPage` / `SettingsPage`，属后续「后台设置 / 缓存与系统」页面，不在统一管理页范围）。

### 后续
- 待建 `AdminPage.tsx`（后台设置）与 `SettingsPage.tsx`（缓存与系统）后，`npm run build` 可全量通过。

## 全息 React UI · 白天 / 黑夜主题切换（web/ 新前端）

**日期**：2026-08-14 · **范围**：全局主题系统，顶栏切换按钮

### 改动
1. **`index.css` 双主题变量层**：`:root, [data-theme="dark"]`（原深色全息值）与 `[data-theme="light"]`（淡紫白 `#f6f3ff` 浅色全息——保留玻璃/光斑/棱镜 hover/渐变文字，仅明度反转）约 30 个变量；全部全局类（body、滚动条、select option、holo-space 光斑网格、卡片、表格、进度条、遮罩、spinner、toast、drop、渐变文字）改 `var()`。
2. **light 覆盖层**：Tailwind 白色 utility（`text-white/30~90`、`bg-white/5~15`、`border-white/5~20` 及 hover 变体）在浅色下不可见，新增 16 条 `[data-theme="light"] .text-white\/60 {…}` 规则映射为主题变量（非 @layer 规则优先于 Tailwind 的 @layer utilities，无需 !important）。
3. **按钮接线**：`AppLayout` 顶栏 chips 区最右新增 ☀️/🌙 胶囊按钮（文案随主题显「白天/黑夜」）；header 硬编码深色改 `bg-[var(--header-bg)]`；`App.tsx` 管理 theme state（localStorage `docchk_theme` 初始化）并同步 `document.documentElement.dataset.theme`；`index.html` head 内联防闪烁脚本（首帧前设主题，默认 dark）。

### 验证
- `tsc --noEmit` 零新增错误；Tailwind CLI 独立编译 `index.css` 产物确认双变量块 + 16 条 light 覆盖规则（含 hover 变体）全部正确生成（minify 后 `[data-theme=light]` 去引号属正常优化）。

### 备注
- 后端 `app.py` 仍挂载旧 `static/index.html`；React 版产物 `web/dist` 未接入，待 AdminPage/SettingsPage 全量 build 后统一部署。

## 全息 React UI · 后台设置 / 缓存与系统页建成 + 正式部署上线（web/ 新前端）

**日期**：2026-08-14 · **范围**：两个缺失页面补齐 + 全量构建 + 部署

### 改动
1. **AdminPage.tsx（后台设置 🛡️）**：
   - 标准检测规则（`config/rules.json`）：meta 说明、global 三字段（单文件上限/问题上限/跳过隐藏）、word/excel/textnorm 三分区折叠卡，每规则行 = 启停开关 + 名称 + 严重级下拉 + 整改建议输入；保存走 `POST /api/rules`、恢复默认走 `/api/rules/restore`。
   - 全局运行设置（`config/settings.json`）：ui/detection/report/parse/log_cache 五组，字段按类型自动渲染（布尔→开关、数值→数字输入、枚举→中文下拉），数组字段只读跳过；保存/恢复默认对应 `/api/settings`。
2. **SettingsPage.tsx（缓存与系统 🗂️）**：统计卡（文件/问题/缓存数/缓存大小）+ 系统信息（版本/模式/后端地址/上次检测）+ 维护操作（清理缓存显示条数、清空检测数据走确认弹窗）+ 离线安全说明。
3. **部署**：`vite build` 全量通过（63 模块）→ 旧单文件版备份至 `static/_legacy/index.html` → `cp -r web/dist/* static/`（index.html + assets/），FastAPI 根挂载实时读盘无需重启。

### 验证
- `tsc --noEmit` **全绿（零报错）**；`vite build` 成功（CSS 34KB / JS 207KB）。
- HTTP 冒烟：页面 200、`/assets/index-*.js|css` 完整返回（字节数与产物一致）、`/api/rules` `/api/settings` `/api/overview` 均正常。
- **React 版前端（5 导航页全齐 + 明暗主题）已正式上线 http://127.0.0.1:8501**。

## 启动工具.bat 更新（适配 React 全息渐变版）

**日期**：2026-08-14 · **范围**：启动脚本 + 桌面入口

### 改动
1. **`启动工具.bat`** 重写（由 `_gen_bat.py` 以 GBK + CRLF 生成，可复跑）：
   - title / banner 对齐「文档核验中心（离线保密版）— 全息渐变版」；
   - **新增前端产物自动同步**：启动时比较 `web\dist\index.html` 与 `static\index.html` 的 LastWriteTime，新构建产物自动 `Copy-Item` 到 `static\`，无需手动拷贝、无需重启服务；
   - 保留原逻辑：8501 端口幂等（已运行则只开浏览器退出）、Python 4 级环境选择（便携版→.venv→WorkBuddy venv→系统 PATH）、延迟 3 秒开浏览器。
2. **桌面入口** `C:\Users\86135\Desktop\文档核验工具.bat`：仅 `call` 项目内脚本（自动继承更新），title 对齐新版文案。

### 验证
- 编码：GBK 解码无乱码、78 行 CRLF（中文 bat 铁律）；
- 逻辑：同步分支时间戳比较正确（dist 15:10:58 < static 15:11:12 不误触发）；幂等分支端口判断正确；
- 真实冒烟：`cmd /c` 执行返回 0，输出「服务已在运行，正在打开浏览器…」，中文显示正常。

---

## 中英文错别字 / 语法 / 词汇错误检查规则（textnorm 扩至 10 项）

**日期**：2026-08-14 · **范围**：`checkers/textnorm_checker.py` + `dictionaries/` 新增 3 个词库 + `config/rules.default.json`

### 改动
1. **新增 3 条规则**（`config/rules.default.json` textnorm 段落，1.0.0 → 1.1.0）：
   - `tn_en_typo`（严重）疑似英文拼写错误
   - `tn_grammar`（严重）疑似中英文语法错误
   - `tn_vocab`（一般）疑似中英文词汇搭配不当
2. **新增 3 个本地词库**（纯文本、UTF-8、可自由增删，下次检测自动生效）：
   - `en_typo.txt`：79 条英文错词（错误|正确），整词匹配 + 自动兼容 ing/es/ed/ly/s/d 词缀变形（recieved→received），词缀随建议保留
   - `grammar.txt`：36 条语法正则 —— 中文（助词/副词重复「的的」、句式杂糅「根据…显示」「通过…使」、关联词误配「只要…才」「只有…就」「不但…反而」「虽然…而且」「因为…因此」）+ 英文（主谓一致 I is/you was、情态动词+has、have went、不规则动词过去式、比较级重复、冠词误用 a/an 带发音例外表、双重否定、between…and I、介词搭配 according with 等）
   - `vocab.txt`：23 条词汇正则 —— 中文（动宾搭配「改善…水平」「提高…力度」、语义赘余「因为…的原因」「多次反复」）+ 英文（irregardless、despite of、could of、comprised of、the fact that 冗词、very unique、fewer/less 可数不可数等）
3. **解析器健壮性**：`_load_patterns` 改用 `rsplit('|', 1)` 切分（正则内部交替符 `|` 不再误伤）；`_load_en_typo` 返回 (正则, 词根, 正确拼写) 三元组精确还原词缀
4. **样例覆盖**：`make_samples.py` 新增英文错词/语法/词汇命中段落 + 中文句式杂糅/关联词/搭配不当命中段落；`dictionaries/README.txt` 同步文档

### 验证
- 规则覆盖：WORD 20/20、EXCEL 13/13、TEXTNORM **10/10 全部命中**（新增 3 类：英文拼写 ×3、语法 ×6、词汇 ×5，命中均为预期样例）
- 好样例零新增误报；Word 报告生成与结构校验通过（52.2 KB）
- 回归 `tests/_tpl_e2e2.py` **23/23 通过**
- 纯本地正则 + 词库匹配，零联网、零 AI，符合离线保密要求；规则可在「后台设置」页开关/调级


## 2026-08-14 · 规则与词库统一管理页：新增「内置标准规则」与「内置词库」两个标签
**范围**：`checkers/dictionary_manager.py` + `app.py` + `web/` 前端 5 个文件 + 部署产物

### 改动
1. **后端词库管理模块** `checkers/dictionary_manager.py`：`DICT_META` 映射 10 个内置词库（colloquial/redundant/confusable/ambiguous/typo/en_typo/abbrev/units/grammar/vocab → 对应 textnorm 规则键与标题）；`list_dictionaries()` 返回文件清单（文件名/标题/关联规则/词条数/大小），`read_dictionary()` / `save_dictionary()`（白名单校验 + 原子写 tmp+os.replace，上限 2MB）
2. **后端 API**（`app.py`）：`GET /api/dictionaries`（词库清单）、`GET /api/dictionaries/{name}`（读全文，非法文件名 404）、`POST /api/dictionaries/{name}`（body `{content}` 保存，UTF-8 无损）
3. **前端**：UnifiedPage 由 3 标签扩为 5 标签——新增「内置标准规则」（⚙️ `#unified/builtinRules`）与「内置词库」（📖 `#unified/builtinDicts`）
   - `BuiltinRulesTab.tsx`：word/excel/textnorm 全部分区展示（默认折叠，可展开），每规则 = 启停开关 + 严重级别下拉 + 整改建议编辑 + 说明；「保存内置规则」写 config/rules.json、「恢复默认」调 /api/rules/restore
   - `BuiltinDictTab.tsx`：10 个词库文件列表（词条数/大小/关联检测项），展开后全文编辑（textarea）+ 保存；编辑提示与格式说明
4. **冒烟测试重写**：`tests/_uni_dom_smoke.js` 改为静态资源冒烟（不依赖 jsdom，旧版 DOM 标记与 jsdom 均已失效）：校验部署产物 + 5 标签 + 内置规则/词库文案与 API 接入

### 验证
- `GET /api/dictionaries` 返回 10 文件，词条数准确（grammar 36 / vocab 24 / en_typo 79 等）；GET 全文 UTF-8 无损；POST 保存原子写生效（模拟前端链路 追加→保存→恢复 内容一致）
- 非法文件名（如 evil.txt）返回 404 被白名单拦截
- 检测回归：TEXTNORM 10/10、WORD 20/20、EXCEL 13/13 全部命中；`tests/_tpl_e2e2.py` 23/23 通过（自定义规则/词库链路不受影响）
- 前端构建 tsc 零错误；部署后页面 200，`tests/_uni_dom_smoke.js` 26/26 通过
- 备注：期间发现 PowerShell 经 Invoke-RestMethod 回传中文会破坏 UTF-8（曾误写 grammar.txt），已用 Python 链路恢复并改用 UTF-8 显式编码验证

## 2026-08-14 · 后台设置与规则词库统一管理：去重整合 + 双向联动
**范围**：`web/src/pages/AdminPage.tsx` + `README.md` + 部署产物

### 改动
1. **去重整合**：后台设置删除「标准检测规则」卡内 word/excel/textnorm 三分区规则编辑（启停/级别/建议），该能力已由统一管理页「内置标准规则」标签完整承载；规则/词库配置入口全站仅保留【规则与词库统一管理】一处（侧边栏 🗄️）
2. **职责划分**：后台设置保留「检测全局限制」卡（config/rules.json 的 global 段：单文件上限/单文件问题上限/跳过隐藏文件——全局生效参数）+「全局运行设置」卡（config/settings.json 五组）；卡片内新增「前往规则与词库统一管理 →」跳转按钮（#unified/builtinRules），并在描述文案中注明职责边界
3. **双向联动（零改造，机制天然保证）**：`load_rules()`/`load_settings()` 每次检测与每次 API 请求均实时读盘（无内存缓存），故后台设置保存的 global 参数与统一管理页保存的规则/词库，在**下一次检测即时生效**，无需重启服务，两处配置读的是同一份文件，不存在数据分叉
4. **界面优化**：README「规则配置」章节同步更新职责说明；NAV 维持 5 项无重复菜单

### 验证
- 静态冒烟 `tests/_uni_dom_smoke.js` 扩为 33 项：新增后台去重断言（新卡片文案存在、旧卡片标题「标准检测规则」与旧分区「Word/Excel 检测」已去除、统一管理 5 标签保留），33/33 通过
- 联动冒烟：后台改 max_file_size_mb 100→101 保存读回一致、skip_hidden_files 取反联动一致、统一管理保存后后台设置读回一致、settings 并发数保存读回一致——随后全部恢复默认值（100/True/800）
- 构建 tsc 零错误，部署产物仅 index-CmoTA7m7.js + index-DqldXl7Y.css，页面 200

## 2026-08-14 · 后台设置新增「界面主题配置」：配色方案 + 自定义强调色（全息主题系统）
**范围**：`web/src/lib/theme.ts` + `index.css` + 11 个组件 + `AdminPage.tsx` + `App.tsx` + `config/settings_manager.py`/`settings.default.json` + `types.ts`

### 改动
1. **主题引擎** `web/src/lib/theme.ts`：配色方案（holographic 全息渐变默认 / dark 深色收敛）+ 自定义强调色（hex，空 = 方案默认）；`applyTheme()` 注入 `<html data-scheme>` + `--accent-rgb` 等 CSS 变量，实时预览与全局统一
2. **CSS 变量体系**：新增 accent 派生变量（--glow-btn/card-sm/md/lg/tab/input/hover、--border-accent(/-soft)、--glow-accent-soft），品牌三色渐变收敛为 --holo-c1/c2/c3；`[data-scheme="dark"]` 覆盖层保留宇宙基底 #0a0a1f/#1a0b2e、收敛光斑、渐变文字改冷色系
3. **11 个组件去硬编码**：HoloButton/Card/Input/Switch/Badge/Modal、TabBar、Sidebar、AppLayout、FileChip、TagChip、TemplateImportTab 的紫色光晕/边框全部改 var()（含 tailwind var+/opacity 不支持问题的处理：新增 --border-accent-soft）
4. **后台设置「全局运行设置」内新增分区「界面主题配置」**：配色方案双卡选择（含缩略条与「当前」徽章）、强调色板（默认/6 预置色/取色器 input[type=color]）、实时预览面板（渐变文字/光晕按钮/开关/进度条/状态边框）、「恢复全息渐变默认主题」一键重置（立即应用+保存）；ui 组旧「theme_accent」下拉移除（避免与主题分区重复）
5. **持久化与全局应用**：settings 新增 `ui.theme_scheme`（默认 holographic）/ `ui.accent_color`（默认 ""）；App.tsx 挂载即读 settings 应用主题，刷新自动恢复；保存走既有 POST /api/settings（deep_merge 自动补齐新字段，无需改 API）

### 验证
- `GET /api/settings` 返回新字段；POST 保存 dark+#22c55e 读回一致，重置回 holographic+""；settings.json 落盘正确
- 构建 tsc 零错误；静态冒烟 `_uni_dom_smoke.js` 扩至 45 项全过（含主题分区文案、accent 变量、data-scheme 断言）
- 检测回归不变：TEXTNORM 10/10、WORD 20/20、EXCEL 13/13、`_tpl_e2e2.py` 23/23
- 不破坏基底：强调色只改光晕/高亮/边框/进度条派生变量，--bg/--bg-deep 不受影响；与规则词库统一管理无功能重叠

## 2026-08-14 · 基于《资产评估准则术语2020》创建术语词库与检测规则
**来源文档**：C:\Users\86135\Downloads\2025100902365143.pdf（18 页，72 条术语）
**范围**：`dictionaries/asset_terms.txt` + `checkers/textnorm_checker.py` + `config/rules.default.json` + `checkers/dictionary_manager.py` + `tests/make_samples.py`

### 改动
1. **新增内置词库 `dictionaries/asset_terms.txt`**（依据中国资产评估协会《资产评估准则术语2020》）：
   - 术语参考区：72 条规范术语 + 精简定义（# 注释，不参与检测，供管理页查看与人工比对）
   - 检测区：11 条术语误写 / 非规范表述变体（正则|建议），均为有据变体：现金流折现法→现金流量折现法、现金流量贴现法→现金流量折现法、评估基准日期→评估基准日、重置成本法→成本法、收益现值法→收益法、委托方→委托人 等
2. **新增 textnorm 规则 `tn_asset_terms`（一般）**：「疑似资产评估术语表述不规范」，已同步 config/rules.json 运行时副本（11 项规则）；词库在 dictionary_manager.DICT_META 登记，统一管理页「内置词库」与「内置标准规则」自动展示
3. **检测链路**：textnorm_checker 加载 asset_terms（_load_patterns 机制，正则内部 | 不受影响），新增 _check_asset_terms；仅变体命中，规范术语写法零误报
4. **样例与测试**：make_samples.py 新增命中段落（现金流折现法/评估基准日期/委托方/重置成本法/收益现值法）

### 验证
- TEXTNORM **11/11 全部命中**（新增类 4 处命中：现金流折现法、评估基准日期、重置成本法、收益现值法），WORD 20/20、EXCEL 13/13、`_tpl_e2e2.py` 23/23
- 好样例零误报：现金流量折现法/评估基准日/委托人/成本法/收益法/企业整体价值 等规范写法均不命中
- 安装 pypdf 后 PDF 检测链路恢复：sample_pdf_bad.pdf 解析命中 14 条（位置「第 N 页」OK），扫描/加密 PDF 正常识别为不可解析
- API：/api/dictionaries 返回 asset_terms.txt（11 条，对应 tn_asset_terms），/api/rules textnorm 11 项；页面 200

## 2026-08-14 · 语句通顺度智能检测规则集（并入文本自动校验链路）
**范围**：`checkers/fluency_checker.py` + `dictionaries/fluency.txt` + `config/rules.default.json`（fluency 段）+ `config/settings_manager.py` + word/excel/pdf_checker + `app.py` + 前端 CustomRulesTab / ReportTab / api / types

### 功能
1. **独立检测规则集（6 类，可单独启停、与自定义正则/敏感词/术语词库并行叠加）**：
   fl_logic 单句逻辑断裂 / fl_incomplete 句子成分残缺 / fl_order 语序混乱 / fl_repeat 重复赘述 / fl_conj 关联词搭配错误 / fl_mixed 句式杂糅
2. **模式词库 dictionaries/fluency.txt**：行格式「正则|说明|灵敏度|等级」，按 # ==== 分区（fl_xxx）归属规则键；按【句】匹配（。！？；切句，支持句首/句尾锚定），非法正则自动跳过
3. **提示等级区分**：模式级 low=建议优化（弱提醒）/ high=强制复核，缺省取规则 severity（fl_mixed 严重、fl_logic/fl_incomplete/fl_conj 一般、fl_order/fl_repeat 轻微）
4. **灵敏度阈值**：后台设置 detection.fluency_sensitivity（loose 放宽=仅低误报核心 / normal 常用默认 / strict 收紧=全量激进），app.py 组 opts 传入 scanner→_dispatch→三个 checker 构造，端到端生效（样例 loose 5 / normal 6 / strict 7）
5. **挂接链路**：word（段落+表格单元格）/ excel（文本单元格）/ pdf（每页）全部接入；命中定位「第 N 页 · 第 M 段」等，报告 by_rule 按规则标题统计自动纳入
6. **报告筛选导出**：POST /api/report 新增 report_filter（all 默认 / fluency 仅语句通顺类），前端报告页「导出范围」下拉；无通顺度问题时返回 400 提示
7. **快捷配置面板**：自定义规则标签页新增【语句通顺规则】HoloCard——整类批量启用/停用、灵敏度三档选择、6 条子规则开关+级别徽章、保存（/api/rules + /api/settings）；提示文案可在词库文件自定义
8. 规则恢复默认（/api/rules/restore）与词库恢复均基于 rules.default.json，新段已含默认

### 验证
- 单元：6 类命中正确、好句零误报（既…又/由于…因此/通过…确认主句 等）、灵敏度档位过滤、禁用单类后不命中
- 回归：TEXTNORM 11/11、WORD 20/20、EXCEL 13/13、FLUENCY 6/6（run_check_test 新增）、_tpl_e2e2.py 23/23
- 端到端：API 上传样例 → /api/issues 通顺度 6 处命中（含段落定位）；报告筛选 82→6 全 fl_*
- 静态冒烟 _uni_dom_smoke.js 61/61；前端 tsc 零错误，构建部署完成，服务重启后 /api/rules 含 fluency 6 条、settings fluency_sensitivity=normal、页面 200

## 2026-08-17 · 复制项目至「文档核验AI」并新增 AI 智能核验（本地 / 联网可选）
**新项目**：E:\AIsoftware\文档核验AI（从 E:\AIsoftware\文档核验 复制，排除 .git/node_modules/__pycache__/构建产物，重新 npm install + build + 部署，服务运行于 127.0.0.1:8501）

### 新增功能（默认关闭，保持离线保密）
1. **AI 智能核验引擎 checkers/ai_checker.py**（仅标准库 urllib，无新依赖）：
   - local 模式：Ollama 原生 /api/chat（默认 http://127.0.0.1:11434），零联网
   - online 模式：OpenAI 兼容 /chat/completions（base_url + api_key，自动补 /v1）
   - 流程：轻量提取文本（Word 段落+表格 / Excel 单元格 / PDF 逐页）→ 按 max_chars 分段（max_requests 上限）→ 逐段调用（全局信号量串行）→ 要求返回严格 JSON 数组，容忍 ```json 包裹；解析失败降级
   - 命中生成 Issue：rule_key=ai_verify、rule_title=AI 智能核验、source=ai；severity 取自模型返回（校验 high/medium/low）
   - 连接失败 / 超时 / 响应异常 → AiError → 降级为提示条目（"AI 核验执行失败：…"），不中断检测
2. **settings ai 组**：enabled(默认 false) / mode / base_url / api_key / model / timeout / max_chars / max_requests；settings.default.json + settings.json 已同步
3. **检测链路集成**：app.py _run_ai_verify（规则检测完成后对非 unreadable 文件追加 AI 阶段，max_workers≤2）；命中后文件状态升为有问题；失败追加提示条目
4. **API**：GET /api/ai/status、POST /api/ai/test（测试连接，成功/失败均有明确信息）
5. **前端**（检测页）：AI 智能核验卡片——不启用/本地 AI/联网 AI 三选（即选即存）、「AI 配置…」弹窗（接口地址/模型名/API Key/超时/分段字数/调用上限 + 测试连接按钮）；开启后上传/扫描 overlay 文案提示 AI 较慢；DropZone 文案按模式显示"零联网/发送至接口"
6. **报告**：AI 问题按 rule_title「AI 智能核验」自动进入报告 by_rule 分布与明细；README 新增 4.1 节说明

### 验证（新项目）
- AI 链路 11 项全过（mock Ollama/OpenAI 服务）：JSON 解析、Word 提取 47 块、local/online 命中、未启用零调用、连接失败降级、test_connection 成功与失败路径
- 服务集成冒烟：settings ai 组、/api/ai/status、/api/ai/test、开启 AI 后上传检测命中 mock 问题（字段完整）、恢复默认关闭
- 回归：WORD 20/20、EXCEL 13/13、TEXTNORM 11/11、FLUENCY 6/6、_tpl_e2e2 23/23
- 前端 tsc 零错误；静态冒烟 71/71（含 AI 面板文案与 API 接入断言）
- 部署结构修正：assets 须位于 static/assets/（Copy-Item 平铺 bug 已修正）；服务重启后页面 200、ai.enabled=False

### 注意
- 原项目服务已不在运行（新项目独占 8501）；如需恢复原项目服务，两项目端口冲突时改其一（app.py 底部 uvicorn.run port）
- 联网 AI 需用户自备 API Key 与接口地址；本地 AI 需本机 Ollama + 已拉取模型

## 2026-08-17 · 本地 AI 连通性实测（Ollama + qwen3:8b + 核验系统 8501）
- 部署检查：Ollama 0.32.14 已安装；用户环境变量 OLLAMA_MODELS=D:\语言ai模型\MX-Qwen3（qwen3:8b，Q4_K_M，5.2GB）
- **修复**：serve 进程未继承 OLLAMA_MODELS（wmic 传参中文路径乱码）→ 模型列表为空。改经批处理文件（GBK 编码）set 环境变量后启动，qwen3:8b 正常可见
- **修复**：test_connection 原判定"响应必须含 ok"，qwen3:8b 回客套话被误判失败 → 改为"服务可达且返回非空响应即成功"（ai_checker.py test_connection）
- 硬件：无 NVIDIA GPU，纯 CPU 推理，约 2 分钟/段（3000 字段会超 120s）
- 调参并写入 settings：ai.enabled=true、mode=local、model=qwen3:8b、timeout=300、max_chars=1500、max_requests=3
- 端到端实测（sample_word_bad.docx）：/api/ai/test 成功；上传检测 92 条问题中含 AI 命中 10 条（high：编号重复/事实错误 300元vs300万元/前后矛盾；medium：内容缺失/语句不通顺/错别字/术语缺失/表意不清）——语义级二次核验真实可用
- 性能提示：CPU 机型建议换 qwen3:4b 或更低量化以提速；AI 阶段慢属正常

## 2026-08-17 · AI 参考资料（参考知识注入 / 伪学习）
背景：用户询问本地 AI 是否具备学习能力。原生 Ollama 模型无记忆，AI 核验为无状态调用。实施「参考知识注入」：上传标准/词汇/规范文件，每次 AI 核验自动携带，按参考标准核查——这是 CPU 机器上最贴近"学习"的可行方案。

### 实现
1. **存储**：config/ai_refs/（上传时提取为纯文本 .txt + meta.json 记录 name/chars/enabled/updated）
2. **支持格式**：.txt / .md / .csv / .docx（段落+表格）/ .pdf；>5MB 拒绝；提取文本 <10 字符拒绝；同名覆盖
3. **注入机制**（checkers/ai_checker.py）：
   - _SYS_PROMPT 增加：有参考资料时严格按标准核查，不符视为问题
   - _messages(text, ref_text) 插入第二条 system 消息携带参考文本
   - _load_ref_text(ref_max_chars)：合并启用条目按字符上限截断（默认 2000）
   - settings ai 组新增 ref_enabled（默认 true）、ref_max_chars（默认 2000）
4. **API**：GET /api/ai/refs、POST /api/ai/refs/upload（multipart）、/api/ai/refs/toggle、/api/ai/refs/delete
5. **前端**（UploadTab AI 配置弹窗新增「参考资料」区块）：携带开关 + 参考字数上限 + 列表（启用勾选/删除/字符数/更新时间）+ 上传按钮 + 空态提示

### 验证
- 后端单测：docx 段落提取、txt 保存、列表、合并截断、启停生效（关闭后不再注入）、删除
- 端到端实测（qwen3:8b）：上传「评估书写规范.txt」（金额必须用万元/ZKB 定义/编号连续/引用须全称等 6 条）
  → 核验 sample_word_bad.docx 命中 9 条，其中明确依据参考标准：
  「金额单位应统一使用万元，设备采购款的『元』单位错误」（规范第 1 条）、
  「未注明 ZKB、API、CRM 等术语的全称定义」（规范第 6 条）
- 前端 tsc 零错误；静态冒烟 80/80（新增参考资料 8 项断言）；页面 200
- 构建部署：index-BzgPWoxg.js + index-BrlSST-c.css（修复 Copy-Item 目标目录缺失导致的部署失败）

### 说明
- 参考资料仅存本机 config/ai_refs/，不随文档外发（online 模式会随请求发送至所配接口——与文档文本同等对待）
- 参考文本每次核验重新读取，改动即时生效；AI 仍无持续记忆，参考注入是"每次携带"式学习

## 2026-08-17 · 规则与词库页 UI 简化排版
- 语句通顺规则面板从「自定义正则规则」标签移至「内置标准规则」标签（新增第 4 分区「语句通顺检测」，含灵敏度下拉 + 启用全部/停用全部；自定义规则页回归纯自定义内容）
- 全局瘦身：删除各页顶部冗余灰色说明文字（keyword/regex 说明、命中标记说明、desc 说明等）；分组头下拉与删除按钮收紧（h-8→h-7、圆角/间距统一）；表格行距 py-2→py-1.5；顶部操作按钮改 size=sm
- 内置词库：头部合并「条数 + 大小」徽标、去掉「对应检测项」冗余文案
- 验证：tsc 零错误、构建通过、静态冒烟 70/70（断言随 UI 同步更新）

## 2026-08-17 · AI 模型下拉选择 + AI 智能创建（对话式/文档自建规则词库）
- AI 配置弹窗模型名：本地模式自动读取 Ollama 已安装模型列表（GET /api/ai/models），下拉选择 + 手动输入兜底 + 刷新按钮；Ollama 未运行显示提示
- checkers/ai_builder.py：对话式创建（自然语言→词库分组+规则 JSON）、文档自建（复用 _extract_ref_text 提取 txt/docx/pdf 文本 → AI 通读提取术语与规范）；JSON 对象解析容忍 ```json/前后缀；非法正则拦截；数量上限（词条40/规则30）
- 端点：POST /api/ai/build/dialogue、/api/ai/build/doc（multipart，≤10MB）；复用核验 AI 配置（不要求核验开关开启）
- 前端：规则与词库页新增第 6 标签「AI 智能创建」（🤖）：对话输入卡 + 文档上传卡；生成结果预览（词库分组表 + 规则表，逐条勾选），「加入词库/加入规则」写入现有自定义词库/自定义规则
- 验证：解析单测 6 项全过；tsc 零错误；构建通过（index-OlKxSV4v.js + index-DvWCwf6K.css）

## 2026-08-18 AI 配置本地化联动与 AI 智能创建
- 本地模型同步扫描：新增 list_local_models（扫描 Ollama /api/tags，10s 缓存）与 resolve_local_model（配置模型不在本机时自动同步为首个可用模型），检测/测试连接/生成三条链路全部接入。
- AI 配置弹窗：打开自动扫描本机模型并自动选中；显示「已同步扫描本机 Ollama：N 个模型」；模型下拉支持手动输入（模型不在列表时自动切换为手输）。
- 修复 start_ollama.bat 模型目录漂移：OLLAMA_MODELS 已改为 D:\语言ai模型\deepseek-r18b，bat 按用户环境变量重建。
- AI 智能创建（对话式 + 文档自建）：后端 ai_builder.py（build_dialogue / build_from_doc），前端 AiBuildTab（第 6 标签，结果预览勾选后加入词库/规则）。
- 长任务适配：qwen3 系本地模型的思考链无法硬禁（模板层强制），生成超时下限放宽到 30 分钟，前端提示 5-20 分钟；文档文本截断 2500 字；num_predict=10000 保证思考+输出额度。
- JSON 容错：修复尾随逗号、字符串内裸引号、`json 包裹、多个 JSON 候选、多余字段（name_en 等）导致的解析失败。
- 冒烟 71/71 通过（新增 aiModels/模型下拉断言）。

### 2026-08-18 续：qwen3:8b 提速与解析修复
- 发现本地目录含官方 qwen3:8b（Ollama 原生支持 think:false 禁思考）：生成任务从 8-25 分钟降至 10-30 秒；settings.ai.model 已切换为 qwen3:8b，配置下拉与本地扫描均可选。
- 修复 _extract_json_obj 候选遍历顺序：由「内层优先」改「外层优先」，避免命中最内层 entry 碎片导致词库/规则全空。
- deepseek-r1:8b 为自定义 qwen3 变体，思考链无法硬禁（Ollama 0.32.14 对非官方 tag 不支持 think/budget），曾致 8-25 分钟/随机 500/空结果；现主模型为 qwen3:8b，兼容逻辑保留。
- start_ollama.bat 重建：OLLAMA_MODELS=D:\本地ai模型（用户环境变量真实值，此前误读为「语言ai模型」）。
- 实测：对话式 13.8s（金额单位→元/high）；文档自建两段式：词库「术语与缩写」2 条（ZKB/API）+ 规则 4 条（金额单位/日期格式 regex/口语化/编号连续）。
- 单测 8 项 + 静态冒烟 71/71 全过。
### 2026-08-18 续2：核验等待界面（实时进度条 + AI 思考过程动态面板）
- 后端任务化：POST /api/upload 与 /api/scan_folder 改为异步返回 {ok, task_id}，后台线程执行；新增 GET /api/task/{tid}（进度/阶段/日志/状态）与 POST /api/task/{tid}/cancel（置取消 + 删除任务缓存文件），任务上限 30。
- 进度真实化：word/excel/pdf 三检查器注入 ProgressHook（解析/页码/格式/通顺度/词库/汇总阶段与百分比），无假进度；多文件合并后规则阶段 0-90%，AI 阶段 90-100%，AI 启动即切换「AI 智能核验」阶段。
- 前端等待页：新增「核验中」标签（⏳）与 WaitingTab（800ms 轮询）、TaskProgress 组件（百分比 + 7 阶段步骤指示）、ThinkingLog 组件（AI 思考过程滚动日志，默认展开可折叠、贴底跟随）；核验完成自动跳转错误详情；核验中禁止跳转报告页（toast 提示）；异常/取消显示提示且不污染结果库；思考日志仅前端展示，Word 报告不含。
- E2E 验证：真实上传 docx/xlsx/pdf → 进度递增、阶段序列与日志（工作表/页码/PDF/解析）齐备、done 落库、报告导出 50KB 无日志、取消后状态保持 cancelled 且结果库不受影响、AI 阶段 0.5s 内切换至 91% + 启动日志。
- 测试：静态冒烟 71/71 通过；_det_dom_smoke.js 因 React+ESM 产物无法在 jsdom 渲染而停用（文件头注明替代验证路径）。
### 2026-08-18 续3：修复「取消核验后 AI 仍在后台运行」
- 根因：_run_ai_verify 用 ex.map 一次性提交全部 AI 任务，取消仅 break 结果收集循环，已提交/正在执行的 _one（ai_check_file 阻塞调用 Ollama）无法中断。
- 修复一（app.py）：改 ex.submit 逐个提交；每次取结果前检查 _task_alive，取消时 cancel 剩余 futures 并退出；进入 AI 阶段前同样先检查取消。
- 修复二（ai_checker.py）：ai_check_file 新增可选 cancel 回调（返回 True 表示已取消），文件开始与每个分段循环前检查，请求间隙立即中止。
- 验证：AI 阶段中取消 → 3s 内日志停止增长、状态 cancelled；静态冒烟 71/71；run_check_test 报告生成正常。
### 2026-08-18 续4：本地AI模型自学习记忆（全程离线 · 仅学习人工确认样本）
- 原则：AI 绝不自动采集文档内容；样本只能由用户主动添加——检测结果「标记正确」按钮（人工确认）或自学习页手动粘贴正确内容。
- 后端 checkers/ai_memory.py：样本/学习记录存 config/ai_memory/（本机）；学习仅调本地 Ollama（复用 ai 组 base_url/model，强制 local、think=False，超时 900s），提炼标准表述 → 词库条目+校验规则，自动合并到 wordbanks.json / custom_rules.json 的「本地AI学习」分组（source=ai_learning 标记，启用即参与检测）；正则预编译校验、重复条目去重、失败自动重试一次。
- 管理能力：样本查看/启用禁用/删除/单条立即学习/学习失败友好提示（模型算力不足→建议换 qwen3 系列或稍后重试）；产出词条与规则可启用禁用/删除（同步改词库与规则文件）；总开关（关闭后禁止添加/学习）；批量清空仅删学习数据（保留用户手动导入编写的规则词库）；记忆数据不进检测导出报告。
- 前端：规则与词库统一管理新增「本地AI自学习」标签（🧠）：统计卡片（样本/产出数）、总开关、添加样本表单、样本卡片列表、产出列表（词条/规则徽标+开关+删除）；检测页错误详情每行新增「标记正确」按钮一键入样本。
- 修复 _extract_json_obj：模型输出损坏（截断/重复）时原逻辑误取最内层碎片对象，改为「覆盖范围最大候选优先」。
- 修复 settings.json ai.model 回退为 deepseek-r1:8b（该 tag 禁思考无效致空输出）→ 切回已验证的 qwen3:8b。
- 验证：E2E 全链路（添加→学习 39s→合并 5 词条+5 规则→开关同步→样本删除→报告不含样本/「本地AI学习」→批量清空仅清学习数据→总开关拒绝添加）；文档自建/对话式生成回归通过；静态冒烟 84/84；run_check_test 报告生成正常。
