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

Transoria 是一个本地小说翻译桌面应用。导入 EPUB / TXT，完成术语整理、翻译、校对和输出重建；模型请求使用你自己的 API Key。

### 下载

最新版：**[GitHub Releases](https://github.com/oodadoudou/Transoria/releases)**

- **macOS**：下载 `Transoria.dmg`，将应用拖入 `/Applications/`。首次启动若被 Gatekeeper 拦截，请查看对应 Release 的 macOS 说明。
- **Windows**：下载 `Transoria-windows.zip`，先在文件属性中解除锁定，再解压到普通可写目录并运行 `Transoria.exe`。

问题反馈 QQ 群：**1104197845**。

### 核心能力

- **翻译工作流**：EPUB / TXT 分块翻译、同结构输出、术语注入、文本保护、替换规则和中断续跑。
- **质量检查与校对**：识别低置信度、原文残留、术语异常、疑似重复和模型异常；支持单条、批量及筛选结果重译。
- **模型、Prompt 与预设**：支持主流供应商及 OpenAI 兼容接口，并可将模型、Prompt 和语言组合成一键切换的工作流预设。
- **术语提取与审查**：生成术语 XLSX 和参考文本，执行多轮审查、表格编辑并导入翻译术语表。
- **请求记录与恢复**：查看耗时、token、回复和失败原因；任务停止、失败或应用重启后可继续处理剩余内容。
- **EPUB / TXT 工具**：批量替换、压缩、合并、格式转换、元数据编辑和 EPUB 修复。

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
python app.py
```

#### Windows PowerShell

```powershell
git clone https://github.com/oodadoudou/Transoria.git
cd Transoria
python -m pip install -e ".[gui,dev]"
cd frontend; npm install; npm run build; cd ..
python app.py
```

</details>

### 使用声明

Transoria 只提供本地翻译辅助能力，不拥有或分发任何原作及译文版权。请仅处理你有权使用的内容，并遵守所在地法律及发布平台规则。

---

## English

Transoria is a local desktop app for novel translation. Import EPUB / TXT files, manage terminology, translate, proofread, and rebuild the final output using your own model API keys.

### Download

Latest builds: **[GitHub Releases](https://github.com/oodadoudou/Transoria/releases)**

- **macOS**: download `Transoria.dmg` and drag the app into `/Applications/`. If Gatekeeper blocks the first launch, follow the macOS notes in the corresponding Release.
- **Windows**: download `Transoria-windows.zip`, unblock it in File Properties, then extract it to a writable folder and run `Transoria.exe`.

### Core Capabilities

- **Translation workflow**: chunked EPUB / TXT translation, structure-preserving output, glossary injection, protected text, replacement rules, and resumable tasks.
- **Quality review**: detect low-confidence output, source residue, terminology issues, possible repetition, and model anomalies; retranslate one row, a selection, or filtered results.
- **Models, prompts, and presets**: use major providers or OpenAI-compatible endpoints, and bundle model, prompt, and language settings into switchable workflow presets.
- **Glossary extraction and review**: generate glossary XLSX and reference text, run multi-round review, edit the final table, and import it into Translation.
- **Request logs and recovery**: inspect latency, token usage, responses, and failures; continue unfinished work after stopping, failure, or application restart.
- **EPUB / TXT tools**: batch replacement, compression, merging, conversion, metadata editing, and EPUB repair.

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
python app.py
```

#### Windows PowerShell

```powershell
git clone https://github.com/oodadoudou/Transoria.git
cd Transoria
python -m pip install -e ".[gui,dev]"
cd frontend; npm install; npm run build; cd ..
python app.py
```

</details>

### Usage Notice

Transoria is a local translation-assistance tool and does not own or distribute rights to original or translated works. Only process content you are authorized to use, and follow applicable laws and platform rules.
