# 文档低级错误检查工具（离线保密版）v1.0

> 纯本地、零联网、强保密的 Word / Excel 文档低级错误检查工具。
> 适用于对 **数据安全、隐私合规** 有严格要求的内网 / 离线环境：
> **所有处理均在本地完成，不上传、不下载、不调用任何云端服务。**

---

## 一、产品定位

一份合同、一份标书、一份报表，往往因为「全角空格」「表头空单元格」
「编号断裂」「公式报错」这类低级错误而影响专业度与可用性。本工具基于
**本地规则引擎**对 `.docx` / `.xlsx` 文档做逐层扫描，定位问题并生成可归档的
Word 检测报告，全程 **不依赖网络、不使用 AI 云端、不读取文档内容外发**。

| 维度 | 说明 |
| --- | --- |
| 支持格式 | Word：`.docx`（含 `.xlsm` 同族的宏启用工作簿 `.xlsm`）|
| 不支持 | `.doc` / `.xls`（旧版 OLE 格式）、加密文档、损坏文档 → 友好提示并跳过 |
| 检测方式 | 纯本地规则匹配，无 AI、无联网、无内容上传 |
| 输出 | 界面可视化统计 + 可下载的 Word 检测报告 |
| 运行模式 | 本地 Streamlit 服务，仅监听 `127.0.0.1`（本机回环）|

---

## 二、离线合规与隐私声明（重点）

本工具在设计与实现上满足以下硬性要求，**可逐条审计验证**：

1. **零联网**：源码中不存在任何 `urllib` / `requests` / `socket` / `http` 客户端调用，
   不存在任何 `webbrowser` 外链、任何 CDN、任何字体/图标/脚本的外链引用。
   前端样式、SVG 图标、动画 **全部内嵌** 在 `ui/styles.py` 与 `ui/components.py` 中。
2. **零遥测**：已关闭 Streamlit 使用统计上报
   （`.streamlit/config.toml` 中 `gatherUsageStats=false`，启动脚本同时设置
   环境变量 `STREAMLIT_BROWSER_GATHER_USAGE_STATS=false`）。
3. **零数据外发**：文档内容、缓存、检测结果 **只保留在本地**
   （缓存目录在系统临时区，可在「设置」页一键清空）。报告由本机生成。
4. **只读不写**：工具 **只检测、只标记、只生成报告，绝不自动修改原始文档**。
5. **绑定本机**：服务仅监听 `127.0.0.1`，局域网其他机器无法访问，天然隔离外网。
6. **可审计**：规则以本地 `JSON` 文件形式公开，全部逻辑可人工审阅。

> 自检方法（供安全审计）：在本项目根目录执行
> `grep -rEn "urllib|requests|socket|http://|https://|cdn|webbrowser" --include=*.py .`
> 预期结果应为「无匹配」。

---

## 三、环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11（64 位）。本机启动脚本为 `.bat`，仅 Windows |
| Python | 3.9 ~ 3.13（推荐 3.11 或 3.13） |
| 内存 | ≥ 4 GB |
| 依赖 | `streamlit` `python-docx` `openpyxl`（均在离线包内提供） |
| 网络 | **不需要**，建议全程断网运行 |

---

## 四、快速开始（三种方式）

### 方式 A：双击启动脚本（最简单，推荐）

直接双击根目录下的 **`启动工具.bat`**：

- 自动识别运行环境优先级：`本目录 python\` → `.venv\` → 系统 `python`
- 以离线模式在本机 `http://127.0.0.1:8501` 启动服务
- 自动打开浏览器窗口
- 关闭黑色窗口即停止服务

### 方式 B：已有 Python 环境，手动启动

```bash
# 1) 安装依赖（联网机器一次性准备；离线机器见第五节）
pip install -r requirements.txt

# 2) 启动
python -m streamlit run app.py --server.address=127.0.0.1 --server.port=8501
```

浏览器访问 `http://127.0.0.1:8501`。

### 方式 C：使用虚拟环境（干净隔离）

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m streamlit run app.py
```

---

## 五、离线安装依赖

在 **有网的准备机器** 上把依赖下载为本地 wheel 包，再拷贝到 **离线机器** 安装。

### 1. 在有网机器下载 wheel

```bash
mkdir wheels
pip download -r requirements.txt -d ./wheels
# 如需把 Streamlit 全部传递依赖一并离线化，建议用：
pip download streamlit python-docx openpyxl -d ./wheels
```

> 提示：`pip download -r requirements.txt` 已包含 requirements 中声明的依赖；
> 若目标机器与准备机器 Python 版本不一致，请在相同版本下下载。

### 2. 在离线机器安装

```bash
pip install --no-index --find-links ./wheels -r requirements.txt
```

`--no-index` 强制 pip **不访问 PyPI**，完全本地安装。
之后即可用「方式 A / B / C」启动，全程无需联网。

---

## 六、目录结构

```
文档核验/
├── app.py                      # Streamlit 主程序（界面与流程编排）
├── 启动工具.bat                # Windows 一键离线启动脚本
├── requirements.txt            # 依赖清单（离线 wheel 安装用）
├── .streamlit/
│   └── config.toml             # 离线运行配置（关闭遥测、绑定 127.0.0.1）
├── config/
│   ├── rules.json              # ★ 规则配置（启用开关/严重级别/建议，可改）
│   └── config_manager.py       # 规则加载/保存/恢复默认
├── checkers/
│   ├── base.py                 # 问题/结果数据模型、OOXML 预检、工具函数
│   ├── word_checker.py         # Word 检测引擎（20 项）
│   ├── excel_checker.py        # Excel 检测引擎（13 项）
│   └── scanner.py              # 单文件/批量调度、进度回调、汇总统计
├── report/
│   └── report_builder.py       # Word 检测报告生成（封面/统计/明细/建议）
├── ui/
│   ├── styles.py               # ★ 内嵌 CSS 设计系统（零 CDN）
│   └── components.py           # ★ 内嵌 SVG 图标与组件（零 CDN）
├── tests/
│   ├── make_samples.py         # 生成样例（好/坏/损坏/旧版）文档
│   └── run_check_test.py       # 端到端自检：覆盖全部规则并生成报告
└── reports/                    # 默认报告导出目录（可在设置中修改）
```

---

## 七、使用说明

启动后界面为 **三栏式**（左侧导航 + 中间内容 + 右侧统计），整体为浅色商务风。

### 1. 上传与导入

- **单文件**：点击「文件上传」直接选择 `.docx` / `.xlsx`。
- **文件夹批量**：切到「文件夹」标签，选择整个文件夹，递归扫描其中所有文档。
- **拖拽**：把文件/文件夹拖入上传区即可。
- 不支持的格式（`.doc` / `.xls` / 加密 / 损坏）会被识别并在结果中标为
  「无法读取」，并给出原因，**不会中断批量任务**。

### 2. 运行检测

- 点击「开始检测」，进度条实时显示处理进度。
- 单文件与批量均支持；批量中单个文件失败不影响其余文件。

### 3. 结果查看

- **概览**：总文件数、通过 / 有问题 / 异常 的文件数，问题严重级别分布环形图。
- **文件列表**：每个文件的问题数、状态标签，可点击查看明细。
- **问题明细**：每条问题含「位置 / 描述 / 原文片段 / 修改建议」。
  - **筛选**：按文件类型、严重级别、规则分类过滤。
  - **忽略**：对误报或不关注项点「忽略」，统计中实时剔除。
  - **标记已处理**：确认修改后点「已处理」。

### 4. 规则配置（本地 JSON）

- 规则与词库统一归集在「规则与词库统一管理」页（🗄️）：自定义正则规则、自定义词库、批量导入、内置标准规则（word / excel / textnorm / fluency 启停、严重级别、整改建议）、内置词库（10 个文本词库全文编辑）。
- 后台设置仅保留「检测全局限制」（单文件上限 / 单文件问题上限 / 跳过隐藏文件）与全局运行设置。
- 「保存」写入 `config/rules.json` / `config/settings.json`；「恢复默认」从备份 `*.default.json` 还原。
- 配置文件纯本地生效，保存后**下次检测即时生效**，无需重启服务。

### 4.1 AI 智能核验（可选，默认关闭）

规则检测（格式 / 文字规范 / 语句通顺度）完成后，可追加 **AI 语义级二次核验**（前后矛盾、表意不清、逻辑不通等规则检不出的问题）。

- **本地 AI（零联网）**：本机安装并启动 [Ollama](https://ollama.com)（`ollama serve`），并已拉取模型（如 `ollama pull qwen2.5:7b`）。文档内容不出本机。
- **联网 AI（OpenAI 兼容接口）**：配置服务商接口地址与 API Key（DeepSeek / 通义 / Kimi / OpenAI 等），待核验文本将发送至该接口。
- 入口：检测页「AI 智能核验」卡片选择 不启用 / 本地 AI / 联网 AI，「AI 配置…」弹窗可填 接口地址 / 模型名 / API Key / 超时 / 分段字数 / 调用上限，并支持「测试连接」。
- 命中问题以「AI 智能核验」条目进入问题明细与检测报告（`rule_key=ai_verify`）；AI 服务不可用时自动降级为提示条目，不中断检测。
- 配置存于 `config/settings.json` 的 `ai` 组；**默认关闭**，未开启时全程零联网。

### 5. 导出检测报告

- 进入「报告」页，点击「生成并下载报告」。
- 报告为 `.docx`，含：封面、基本信息、整体统计、规则分布、
  逐文件问题明细、异常文件清单、整改建议。
- 报告保存路径可在「设置」中自定义。

### 6. 设置

- **缓存管理**：查看缓存占用，一键清空（不影响原始文档）。
- **离线合规说明**：内置隐私与离线声明，供审计核对。
- **报告路径**：自定义报告默认保存目录。

---

## 八、检测规则清单

### Word（20 项）

| 规则键 | 名称 | 默认级别 |
| --- | --- | --- |
| `full_width_space` | 多余全角空格 | 轻微 |
| `leading_space` | 段首冗余空格 | 轻微 |
| `trailing_space` | 段尾多余空格 | 轻微 |
| `invalid_whitespace` | 无效空白字符（零宽/不换行空格/垂直制表符等） | 轻微 |
| `multi_space` | 连续多个空格 | 轻微 |
| `punct_mix` | 中英文标点混用 | 一般 |
| `punct_repeat` | 连续重复标点 | 一般 |
| `bracket_unpaired` | 括号引号不配对 | 一般 |
| `empty_heading` | 空标题 | 严重 |
| `empty_paragraph` | 连续空段落 | 轻微 |
| `manual_line_break` | 大量无效换行 | 轻微 |
| `numbering_break` | 自动编号断裂 | 严重 |
| `manual_number_sequence` | 手工序号不连续 | 严重 |
| `number_format_mixed` | 序号格式混乱 | 一般 |
| `table_empty_row` | 表格空白行 | 一般 |
| `table_empty_col` | 表格空白列 | 一般 |
| `table_no_header_repeat` | 跨页表格无重复表头 | 一般 |
| `table_empty_header_cell` | 表格表头空单元格 | 一般 |
| `image_broken` | 图片 / 对象加载异常 | 严重 |
| `blank_redundant_content` | 空白冗余内容 | 轻微 |

### Excel（13 项）

| 规则键 | 名称 | 默认级别 |
| --- | --- | --- |
| `formula_error` | 公式报错（#N/A、#VALUE!、#DIV/0! 等） | 严重 |
| `number_stored_as_text` | 数字文本化存储 | 一般 |
| `format_chaos` | 单元格格式混乱 | 轻微 |
| `empty_row` | 整行空白 | 一般 |
| `empty_col` | 整列空白 | 一般 |
| `empty_header_cell` | 表头空单元格 | 严重 |
| `redundant_merged_cells` | 多余合并单元格 | 一般 |
| `empty_sheet` | 无意义空白工作表 | 一般 |
| `sheet_name_invalid` | 工作表名称异常 | 一般 |
| `trailing_space_cell` | 单元格首尾空格 | 轻微 |
| `full_width_space_cell` | 单元格全角空格 | 轻微 |
| `duplicate_header` | 表头重复字段 | 一般 |
| `hidden_row_col` | 隐藏行列 | 轻微 |

> 部分规则带可调参数（如 `multi_space.min_count`、`punct_repeat.allow_ellipsis`、
> `table_no_header_repeat.min_rows`、`global.max_file_size_mb` 等），
> 可直接在 `config/rules.json` 中修改，无需改代码。

---

## 九、自检与验证

项目内置测试样例与端到端验证脚本，可证明检测引擎覆盖全部规则：

```bash
python tests/make_samples.py        # 生成 好/坏/损坏/旧版 样例文档
python tests/run_check_test.py      # 端到端检查并打印规则覆盖 + 生成报告
```

预期输出（节选）：

```
WORD   命中 20/20，全部命中
EXCEL  命中 13/13，全部命中
报告已生成：.../tests/out/自检报告.docx  (约 47 KB)
报告校验：段落 48 个，表格 10 个 → 结构有效
```

---

## 十、打包为 EXE（可选）

> 说明：Streamlit 是「本地 Web 服务 + 浏览器」形态，打包为单一 EXE 比普通
> 脚本复杂。以下给出 **两种** 可靠方案，**方案 1 优先推荐**（最稳、最易审计）。

### 方案 1（推荐）：便携目录 + 启动脚本（无需 PyInstaller）

1. 在本项目目录放入一个便携版 Python：
   - 把官方嵌入式/安装版 Python 解压到 `python\`（即 `python\python.exe`）。
   - 在「有网准备机器」上对该 python 执行
     `python\python.exe -m pip install --target=./_libs -r requirements.txt`
     （或建好 `.venv` 一并随包分发）。
2. 双击 `启动工具.bat` 即可。脚本会自动识别 `python\` 或 `.venv\`。
3. 整个目录拷到任意离线 Windows 机器即可运行，**全程无联网**。

优点：100% 离线、可审计、启动稳定、无 PyInstaller 兼容坑。

### 方案 2：PyInstaller 打包（单目录）

适合需要「一个文件夹里有 exe」的分发形态。

```bash
# 在有网机器
pip install pyinstaller
pyinstaller --onedir --name 文档核验 ^
  --hidden-import streamlit.runtime ^
  --hidden-import streamlit.web.bootstrap ^
  --add-data "config;config" ^
  --add-data "checkers;checkers" ^
  --add-data "report;report" ^
  --add-data "ui;ui" ^
  --add-data ".streamlit;.streamlit" ^
  app.py
```

打包后在 `dist\文档核验\` 中得到 `文档核验.exe`，但 **仍建议配合
`启动工具.bat` 思路**：让 exe 调用 `streamlit run`，并在打包资源里带上
`config/rules.json`。注意：

- Streamlit 依赖较多 C 扩展与静态资源，若启动报缺模块，按报错补充
  `--hidden-import` / `--collect-submodules streamlit`。
- `--onefile` 单文件模式启动慢且易因临时解压路径问题失败，**不推荐**。
- 打包后仍需在目标机本地运行，依旧满足离线 / 不联网要求。

---

## 十一、常见问题

**Q1：双击启动脚本没反应 / 提示找不到 Python？**
确认已安装 Python 3.9+ 并加入 PATH，或将便携 Python 放到 `python\` 目录。
脚本会依次查找 `python\` → `.venv\` → 系统 `python`。

**Q2：扫描 `.doc` / `.xls` 被跳过？**
旧版 OLE 格式（`.doc` / `.xls`）不在支持范围，请先用 Word/Excel 另存为
`.docx` / `.xlsx`。加密文档同样会被识别并跳过。

**Q3：误报太多怎么办？**
进入「规则」页关闭对应检测项；或在「问题明细」中点「忽略」单独排除。
配置保存在本地 `config/rules.json`，刷新即生效。

**Q4：工具会修改我的原文档吗？**
不会。本工具 **只读不写**，只在本地生成报告，绝不改动原始文件。

**Q5：数据会不会上传？**
不会。已关闭全部遥测，且无任何外联代码，服务仅监听 `127.0.0.1`。
可参考第二节的 `grep` 自检命令人工审计。

**Q6：能处理多大的文件？**
默认单文件上限 100 MB（见 `config/rules.json` 的 `global.max_file_size_mb`），
单文件问题数上限 800 条（防极端大文档卡顿），均可在配置中调整。

---

## 十二、二次开发指引

- **新增检测规则**：在 `checkers/word_checker.py` 或 `excel_checker.py` 中
  实现检查方法，向 `Issue` 列表追加问题；同时在 `config/rules.json` 中登记
  该规则的 `enabled/severity/title/desc/suggestion`，并在 `Issue.rule_key`
  中保持一致。
- **界面微调**：样式集中在 `ui/styles.py`（CSS 变量与设计系统），组件在
  `ui/components.py`（内嵌 SVG 图标）。**新增资源务必内嵌，禁止外链 CDN**。
- **报告模板**：`report/report_builder.py` 中 `ReportBuilder` 负责生成，
  自带表格底纹、重复表头、页码域等，均为纯本地 OpenXML 操作。

---

> 本工具所有代码、资源、文档均本地化，未使用任何云端 API、CDN 或外部服务。
> 适用于对数据保密有严格要求的内部文档质检场景。
