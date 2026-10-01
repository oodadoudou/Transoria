# Transoria

<p align="center">
  <strong><a href="#中文">中文</a></strong> ·
  <strong><a href="#english">English</a></strong> ·
  <strong><a href="https://github.com/oodadoudou/Transoria/releases">Download</a></strong>
</p>

<p align="center">
  <img src="assets/readme/transoria-hero.svg" width="100%" alt="Transoria local EPUB and TXT novel translation workspace">
</p>

## 中文

Transoria 是一个集小说翻译与 EPUB 编辑于一体的本地桌面应用。导入 EPUB / TXT，完成术语整理、翻译、校对和输出重建；也可独立编辑 EPUB 正文、样式、目录和资源。模型请求使用你自己的 API Key。

### 下载

最新版：**[GitHub Releases](https://github.com/oodadoudou/Transoria/releases)**

- **macOS**：下载 `Transoria.dmg`，将应用拖入 `/Applications/`。首次启动若被 Gatekeeper 拦截，请查看对应 Release 的 macOS 说明。
- **Windows**：下载 `Transoria-windows.zip`，先在文件属性中解除锁定，再解压到普通可写目录并运行 `Transoria.exe`。

问题反馈 QQ 群：**1104197845**。

### 核心能力

- **翻译工作流**：EPUB / TXT 分块翻译、同结构输出、术语注入、文本保护、替换规则和中断续跑；EPUB 分页标记后的正文也会参与翻译。
- **质量检查与校对**：按源语言识别低置信度、原文残留、术语异常、疑似重复和模型异常；支持单条、批量及筛选结果重译，并对韩语源任务的单条重译及疑似外语音译还原进行候选质量校验；拉丁语系批量重译会拦截明显的段落错配。相同译文不会重复写入，质量比较请求的 Token 用量可在请求记录中查看。
- **模型、Prompt 与预设**：支持主流供应商及 OpenAI 兼容接口；基础预设可一键切换模型、Prompt 和语言。翻译预设可选高级模式，设置总并发上限，并为各供应商线路分别指定 Prompt 与 RPM 上限；额度不足时等待，有额度的线路可接管尚未发出的分块，首轮失败后可按需使用兜底模型补救。翻译、术语提取和审查运行中切换模型、Prompt 或预设，会等待当前请求收尾，再处理剩余工作；重试和多 Key 轮询均计入 RPM，切换和继续任务保留最近一分钟的额度记录。编辑预设后需主动应用，删除正在使用的模型或 Prompt 会停止受影响的任务并保留进度。
- **术语提取与审查**：生成术语 XLSX 和参考文本，执行多轮审查、表格编辑并导入翻译术语表。
- **请求记录与恢复**：查看耗时、token、回复和失败原因；保留截断或过滤回复中完整且可验证的分段，仅重试缺失内容，并可在任务停止、失败或应用重启后继续处理。
- **EPUB / TXT 工具**：批量替换、压缩、合并、格式转换、支持小说简介的元数据编辑和 EPUB 修复。
- **EPUB 内容编辑**：多文件 XHTML/CSS 标签、章节与样式拆分合并、图片插入、紧凑目录，以及文件右键操作和 Command/Ctrl/Shift 多选；支持批量复制、改名、删除和 ZIP 导出。按标题、分层正则或 XPath 生成目录。Command/Ctrl+F 打开底部紧凑搜索替换栏，输入或切换范围、匹配选项后自动搜索；支持选区和文件范围、忽略行内标签、正则与捕获组替换、跨文件前后跳转，以及带过期检查的批量替换预览；可保存搜索／替换预设并按顺序应用多条替换规则。HTML/CSS 转换规则可预览文件差异并整体撤销。源码与预览可换行、缩放，点击源码或滚动预览可双向定位；全书支持滚动翻页、跨章节连续或分页阅读，保留内嵌样式、字体、书内链接和元素样式查看。
- **书籍维护与保存**：本地草稿、命名检查点、撤销和修改对比；暂存后退出桌面应用可恢复编辑，正式保存可另存或确认覆盖。支持拼写与排版报告、保守 CSS 清理、图片优化、字体子集化与嵌入、封面 metadata 和 EPUB 3 升级。拼写可使用本地词表或 Hunspell 词典；EPUBCheck 需要本机 Java 与检查器 JAR，其他书籍格式导入需要已安装的 `ebook-convert`，这些引擎不会自动下载。

编辑器支持按完整文字列进行左右竖排分页，保留注音、混合书写方向、本地 CSS 导入条件、命名空间和分组目录。固定版式按原始页面尺寸适配窗口，并可预览左右双页。异常 XHTML 可通过 HTML5 规则只读预览；打开和预览不会自动修复原文件，修改后的 XML 仍需通过保存校验。超大章节会提示精确分页上限；原书过宽元素仍可通过连续预览和缩放查看。

### 推荐流程

1. 用「术语提取」生成术语表和参考文本。
2. 在「术语审查」中检查并导入最终术语表。
3. 选择模型、Prompt 或工作流预设后开始翻译。
4. 在「校对」中处理风险条目并按需重译。
5. 重新生成最终 EPUB / TXT 输出。

只需整理电子书时，可直接进入「通用工具 → EPUB 工具」。

<details>
<summary><strong>从源码运行</strong></summary>

需要 Python ≥ 3.11、Node.js ≥ 18，以及推荐使用的 [uv](https://github.com/astral-sh/uv)。

#### macOS / Linux

```bash
git clone https://github.com/oodadoudou/Transoria.git
cd Transoria
uv sync --extra gui --extra dev
cd frontend && npm install && npm run build && cd ..
uv run --no-sync python app.py
```

#### Windows PowerShell

```powershell
git clone https://github.com/oodadoudou/Transoria.git
cd Transoria
python -m pip install -e ".[gui,dev]"
cd frontend; npm install; npm run build; cd ..
python app.py
```

使用 pip / Conda 当前环境时，在仓库根目录运行 `python -m pip install -r requirements.txt`，即可安装运行、测试和打包依赖，再用同一个 `python` 启动。依赖版本统一声明在 `pyproject.toml`。构建桌面应用时，macOS 运行 `python build_macos.py`，Windows 运行 `python build_windows.py`。

</details>

### 使用声明

Transoria 提供本地翻译辅助与电子书编辑能力，不拥有或分发任何原作及译文版权。请仅处理你有权使用的内容，并遵守所在地法律及发布平台规则。

---

## English

Transoria is a local desktop app for novel translation and EPUB editing. Import EPUB / TXT files, manage terminology, translate, proofread, and rebuild the final output using your own model API keys. You can also independently edit EPUB content, styles, tables of contents, and resources.

### Download

Latest builds: **[GitHub Releases](https://github.com/oodadoudou/Transoria/releases)**

- **macOS**: download `Transoria.dmg` and drag the app into `/Applications/`. If Gatekeeper blocks the first launch, follow the macOS notes in the corresponding Release.
- **Windows**: download `Transoria-windows.zip`, unblock it in File Properties, then extract it to a writable folder and run `Transoria.exe`.

### Core Capabilities

- **Translation workflow**: chunked EPUB / TXT translation, structure-preserving output, glossary injection, protected text, replacement rules, and resumable tasks; prose after EPUB page markers is included.
- **Quality review**: detect low-confidence output, source residue, terminology issues, possible repetition, and model anomalies with source-language-aware checks; retranslate one row, a selection, or filtered results, with candidate validation for Korean-source single-row replacements and suspected foreign-language phonetic restorations. Unaccepted candidates remain available for manual comparison in Proofreading. Latin-source batch retranslation blocks obvious cross-segment drift. Identical translations are not written again, and quality-comparison token usage appears in the request log.
- **Models, prompts, and presets**: use major providers or OpenAI-compatible endpoints. Basic presets bundle model, prompt, and language settings; optional advanced translation presets set a shared concurrency ceiling, per-provider routes with their own prompts and RPM limits, and an optional fallback model for failed chunks. Routes with available capacity can take over unsent chunks while rate-limited routes wait. Translation, Glossary Extraction, and Glossary Review drain current requests before applying model, prompt, or preset switches to unfinished work. Retries and key rotation count toward RPM; switching and continuing tasks retain the last minute's request history. Preset edits take effect when explicitly applied. Deleting an in-use model or prompt stops affected tasks while preserving progress.
- **Glossary extraction and review**: generate glossary XLSX and reference text, run multi-round review, edit the final table, and import it into Translation.
- **Request logs and recovery**: inspect latency, token usage, responses, and failures; preserve complete validated rows from truncated or filtered responses, retry only missing content, and continue unfinished work after stopping, failure, or application restart.
- **EPUB / TXT tools**: batch replacement, compression, merging, conversion, metadata editing including book descriptions, and EPUB repair.
- **EPUB content editor**: multiple XHTML/CSS tabs, chapter and stylesheet split/merge, image insertion, compact TOC editing, file context menus and Command/Ctrl/Shift multi-selection with batch duplication, renaming, deletion and ZIP export. Generate TOCs from headings, layered regex or XPath. Command/Ctrl+F opens a compact bottom search/replace bar that updates matches when typing or changing scope/options, with selection and file scopes, ignoring inline tags, regex capture replacement, cross-file previous/next navigation, stale-safe batch previews, and persistent search/replace presets with ordered, undoable multi-rule replacement. Visual HTML/CSS transformation rules preview file diffs and apply as one undoable transaction. Source and preview wrap, zoom and synchronize positions in both directions; wheel navigation turns pages and crosses chapters in continuous or paged book previews, retaining local styles, fonts, internal links, and element-style inspection.
- **Book maintenance and saving**: local drafts, named checkpoints, undo, and change comparison before save-as or confirmed overwrite; staged edits can be restored after restarting the desktop app. Tools include spelling/typography reports, conservative CSS cleanup, image optimization, font subsetting/embedding, cover metadata, and EPUB 3 upgrading. Spelling accepts a local word list or Hunspell dictionary. Optional EPUBCheck needs local Java and a checker JAR; other book-format imports need an installed `ebook-convert`. Neither engine is downloaded automatically.

The editor pages vertical text at complete column boundaries, retaining ruby, mixed writing modes, local CSS import conditions, namespace scope, and grouped TOCs. Fixed layouts fit their authored page dimensions to the window and support paired-page previews. Malformed XHTML uses a read-only HTML5 preview fallback; opening and previewing never repair the source automatically, and edited XML still requires save validation. Oversized chapters display a precise-pagination limit warning; overwide authored elements remain accessible through continuous preview and zoom.

### Recommended Workflow

1. Generate a glossary and reference text with **Glossary Extraction**.
2. Review and import the final glossary with **Glossary Review**.
3. Select a model, prompt, or workflow preset and start Translation.
4. Resolve flagged rows in **Proofreading** and retranslate where needed.
5. Regenerate the final EPUB / TXT output.

For ebook-only maintenance, open **General Tools → EPUB Tools** directly.

<details>
<summary><strong>Run from source</strong></summary>

Requires Python ≥ 3.11, Node.js ≥ 18, and preferably [uv](https://github.com/astral-sh/uv).

#### macOS / Linux

```bash
git clone https://github.com/oodadoudou/Transoria.git
cd Transoria
uv sync --extra gui --extra dev
cd frontend && npm install && npm run build && cd ..
uv run --no-sync python app.py
```

#### Windows PowerShell

```powershell
git clone https://github.com/oodadoudou/Transoria.git
cd Transoria
python -m pip install -e ".[gui,dev]"
cd frontend; npm install; npm run build; cd ..
python app.py
```

For the current pip / Conda environment, run `python -m pip install -r requirements.txt` from the repository root to install runtime, test, and build dependencies, then launch with the same `python`. Dependency versions are defined in `pyproject.toml`. Build the desktop app with `python build_macos.py` on macOS or `python build_windows.py` on Windows.

</details>

### Usage Notice

Transoria provides local translation assistance and ebook editing and does not own or distribute rights to original or translated works. Only process content you are authorized to use, and follow applicable laws and platform rules.
