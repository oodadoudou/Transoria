# Transoria

Transoria 是一个面向小说翻译的桌面应用：把一个装着 EPUB / TXT 的文件夹丢给它，得到翻译完成后的同结构文件夹。附带两个工具——**术语提取**（先扫一遍统计高频专有名词，作为翻译时的术语表）和**批量文本替换**（按规则在大量文件里做查找替换）。

界面支持中英文切换。所有任务在本地运行，由你自己的 LLM API Key 调用模型。

## 截图

![模型配置页](assets/demo/model_page.jpg)

![翻译运行页](assets/demo/translate_run.jpg)

## 下载安装包

最新版本下载：**[GitHub Releases](https://github.com/oodadoudou/Transoria/releases)**

- **macOS** — `Transoria.dmg`，挂载后把 `Transoria.app` 拖到 `/Applications/`。首次启动若被 Gatekeeper 拦截，参考对应 Release 描述里的「macOS 用户须知」。
- **Windows** — `Transoria-windows.zip`。解压前请右键 ZIP → 属性 → 解除锁定 → 应用，再解压到一个普通可写目录（如「文档」或「桌面」），然后双击 `Transoria.exe`。包内 `README_CN.txt` 有完整说明。

## 跑源码（不用安装包）

适用场景：自定义 / 调试 / 在 Linux 上跑 / 安装包遇到问题想绕过。

### 前置依赖

- Python ≥ 3.11
- Node.js ≥ 18
- [uv](https://github.com/astral-sh/uv)（推荐，更快）或 `pip`

### macOS / Linux

```bash
git clone https://github.com/oodadoudou/Transoria.git
cd Transoria

# 后端依赖（含桌面 shell 所需）
uv sync --extra gui --extra dev
# 或： python -m pip install -e ".[gui,dev]"

# 前端构建
cd frontend
npm install
npm run build
cd ..

# 启动桌面应用
python app.py
```

### Windows（PowerShell）

```powershell
git clone https://github.com/oodadoudou/Transoria.git
cd Transoria

# 后端依赖
python -m pip install -e ".[gui,dev]"

# 前端构建
cd frontend
npm install
npm run build
cd ..

# 启动桌面应用
python app.py
```

## 启动后第一步

1. 在「**模型**」页面新增或选择一个 LLM 配置，填入 API Key（DeepSeek / OpenAI / Anthropic / Google / 火山引擎 Ark 等均支持）。
2. 进入「**翻译**」/「**术语提取**」/「**通用工具 → 批量替换**」对应模块的「设置」页，指定输入文件夹与输出文件夹。
3. 回到该模块的「**运行**」页面点开始。

进度、token 用量、失败重试、日志都会在运行页面实时显示。

---

For non-Chinese users: Transoria is a desktop app for novel translation. Drop a folder of EPUB / TXT files in, get translated files out. Bundled tools: glossary extraction (collects recurring terms for consistent translation) and batch text replacement. Local-first, brings your own LLM API key. UI ships with English and Chinese; see English `README_EN.txt` inside the Windows ZIP, or follow the source-run instructions above.
