# 本地离线文档互转工具

![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Pandoc 3.x](https://img.shields.io/badge/Pandoc-3.x-2D2D2D)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
[![Tests](https://github.com/tylmcy/local-doc-converter/actions/workflows/tests.yml/badge.svg)](https://github.com/tylmcy/local-doc-converter/actions/workflows/tests.yml)

一个面向个人使用与作品集展示的本地网页工具，可在 TXT、Markdown 和 DOCX 之间批量转换文档。所有文件都在本机处理，不调用 AI API，不上传云端，也不依赖在线服务。

## 项目状态

当前稳定版本为 **v1.0.1**。转换核心与第一版交互已经冻结，详细变更见 [CHANGELOG.md](CHANGELOG.md)。当前不制作安装包；后续格式范围基本稳定后，再统一评估其他平台的实现与打包方式。

## 功能

- 支持 TXT → Markdown、TXT → DOCX。
- 支持 Markdown → TXT、Markdown → DOCX。
- 支持 DOCX → Markdown、DOCX → TXT。
- 网页批量上传，单批最多 100 个文件、总计 200 MB。
- 自动识别 UTF-8、GBK、GB18030 等常见中文文本编码。
- 通过离线规则识别 TXT 中的中文章节、数字标题、短行标题、列表、简单表格和缩进代码块。
- 尽可能保留标题、段落、列表、表格、代码块、链接和图片语义。
- 每个文件生成 JSON 转换报告，包含结构统计、警告和潜在格式损失。
- DOCX 在进入 Pandoc 前检查 ZIP 路径、解压体积、压缩比、加密、符号链接和 CRC。
- 结果写入本地目录；页面可下载单个文件或包含结果、图片资源和报告的 ZIP。
- 临时文件在每次任务结束后自动清理；已有文件不会被覆盖。

## 运行环境

本项目以 MacBook Air M5（Apple Silicon）为目标环境，推荐：

- macOS
- Python 3.12（项目兼容 Python 3.12 及以上）
- [Pandoc](https://pandoc.org/) 3.x
- [uv](https://docs.astral.sh/uv/) 作为 Python 环境与依赖管理器

首次安装需要联网下载依赖；安装完成后，文档转换过程可完全离线运行。

## 安装

在项目目录打开终端：

```bash
cd local-doc-converter
```

安装系统工具：

```bash
brew install pandoc uv
```

安装 Python 3.12 与项目依赖：

```bash
uv python install 3.12
uv sync --extra dev
```

确认环境：

```bash
uv run python --version
pandoc --version
```

如果不使用 `uv`，也可以用 Python 3.12 创建虚拟环境后执行 `pip install -e '.[dev]'`。

## 启动网页界面

推荐直接运行：

```bash
./scripts/start.sh
```

如果脚本没有执行权限，先运行：

```bash
chmod +x scripts/start.sh
```

也可以手动启动：

```bash
uv run streamlit run app.py --server.address=127.0.0.1
```

浏览器通常会自动打开 `http://localhost:8501`。仓库内的 Streamlit 配置会把服务绑定到 `127.0.0.1`，避免暴露到局域网；本项目代码不会向外部服务发送文档。

## 网页使用方法

1. 上传一个或多个 `.txt`、`.md`、`.markdown` 或 `.docx` 文件。
2. 选择 TXT、Markdown 或 DOCX 目标格式。
3. 填写本地输出目录。为避免误写系统目录，目录必须位于当前用户主目录的子目录中。
4. 点击“开始转换”，程序会先校验输出目录，再开始处理文件。
5. 通过逐文件进度查看当前处理状态。与目标格式相同的输入会标记为“已跳过”，不计为失败。
6. 在“结果概览”和“详细报告”中查看结构统计、编码、警告和错误。
7. 下载单个结果或包含全部结果、媒体资源和报告的 ZIP，也可以直接在访达中打开输出目录。

输出文件默认写入项目下的 `converted/`，并保留原文件名，只替换扩展名。例如 `会议记录.txt` 转 Markdown 后得到 `会议记录.md`。如果同名文件已存在，会生成 `会议记录_2.md`、`会议记录_3.md` 等名称，不覆盖已有文件。

## 命令行使用

命令行适合脚本化和快速验证：

```bash
uv run local-doc-converter examples/input/中文结构示例.txt --to markdown --output examples/output
```

批量转换：

```bash
uv run local-doc-converter examples/input/中文结构示例.txt examples/input/代码与列表.txt --to docx --output converted
```

`--to` 可选值为 `txt`、`markdown`、`docx`。

## 转换流程

```text
输入文件
  → 文件类型、文件名、大小和输出路径校验
  → DOCX ZIP 安全预检（仅 DOCX 输入）
  → TXT 编码检测 / Pandoc Reader
  → Pandoc JSON AST 统一中间结构
  → 结构清理、图片安全检查和统计
  → Pandoc Writer
  → DOCX 中文基础样式补充
  → 结果文件和 JSON 报告
  → ZIP 打包
  → 清理任务临时目录
```

```mermaid
flowchart LR
    A[本地输入文件] --> B[安全、编码与 DOCX 容器校验]
    B --> C[Pandoc JSON AST]
    C --> D[结构清理与统计]
    D --> E[Pandoc Writer]
    E --> F[结果文件与 JSON 报告]
    F --> G[单文件下载或 ZIP]
```

TXT 会先由本地规则整理为结构化 Markdown，再交给 Pandoc 生成 JSON AST。规则优先识别：

- `第一章`、`第二节`、`第3篇`。
- `一、`、`（一）`。
- `1.`、`1.1`、`1.1.1`。
- `-`、`*`、`+` 和数字列表。
- Markdown 管道表格、制表符分隔的简单表格。
- 四空格或 Tab 缩进代码块。
- 前后为空行、不以句末标点结尾的短行标题。

短行标题属于低置信度推断，报告会提示人工核对。

## 转换报告

每个输入文件旁会生成一个 `*_report.json`，主要字段包括：

- 转换是否成功、是否跳过和可理解的错误原因。
- 源格式、目标格式、输出文件和耗时。
- TXT 检测到的编码。
- 标题、列表、表格、代码块、链接和图片数量。
- 处理警告。
- 可能丢失的格式。

批量 ZIP 还包含 `batch_report.json` 汇总报告。某个文件失败不会中断同批次其他文件。

## 支持范围

| 内容 | TXT | Markdown | DOCX |
|---|---|---|---|
| 标题 | 规则推断 | 支持 | 支持常见标题样式 |
| 段落 | 支持 | 支持 | 支持 |
| 列表 | 规则推断 | 支持 | 支持常见列表 |
| 表格 | 简单管道/Tab 表格 | 支持常见表格 | 支持常见表格 |
| 代码块 | 缩进/围栏 | 支持 | 以样式化文本为主 |
| 链接 | Markdown 链接可识别 | 支持 | 支持常见超链接 |
| 图片 | 仅引用 | 本地同目录图片可嵌入 | 转 Markdown 时提取并打包 |

为了保持真正离线，远程图片不会下载；生成 DOCX 时会把远程、缺失或越过文档目录的图片降级为普通链接。

## DOCX 安全预检

DOCX 是 ZIP 容器。程序会在 Pandoc 读取前进行只读、流式预检，不把容器内容提取到磁盘：

- ZIP 条目最多 5,000 个。
- 解压后总大小最多 500 MB，单个条目最多 100 MB。
- 总压缩比最多 200 倍，单个条目最多 1,000 倍。
- 拒绝路径穿越、绝对路径、反斜杠路径、符号链接、加密条目、重复条目和损坏 CRC。
- 宏、OLE 嵌入对象和外部关系不会执行或主动访问，但会写入转换警告。

该预检用于降低异常压缩和恶意 ZIP 结构造成的资源风险，不是病毒或恶意代码扫描器。

## 已知限制

- DOCX 不是精确版式格式转换。页眉页脚、批注、修订记录、文本框、宏、SmartArt、目录域、复杂浮动对象和精确分页可能丢失。
- 复杂合并单元格、嵌套表格和 Word 专有样式可能被简化。
- Markdown 上传到网页时只上传了单个文档，无法自动取得其旁边未上传的本地图片；命令行转换可读取 Markdown 文件同目录内的图片。
- DOCX 转 Markdown 会把媒体提取到与输出同级的资源目录。移动 Markdown 文件时应同时移动该目录。
- TXT 结构识别基于规则而非语义模型，短标题、中文序号列表和标题之间可能存在歧义。
- Markdown 表格必须使用至少三个连字符作为表头分隔语法；工具会把 Pandoc 产生的长分隔线压缩为最短合法形式 `---`，但不能完全删除。
- TXT 目标天然不能保留图片、字体、颜色、表格边框和精确列表样式。
- 当前版本顺序执行批次，优先保证稳定和可解释，不针对超大文档并行加速。
- DOCX 预检不能判断宏、嵌入对象或正文内容是否包含恶意代码；不可信来源文件仍应使用系统安全工具检查。

## 自动化测试

运行全部测试：

```bash
uv run pytest
```

测试包括编码识别、TXT 规则、AST 统计、路径安全、DOCX 容器预检、同名避让、跳过状态、五文件批处理、ZIP 内容、本地图片嵌入与提取，以及在 Pandoc 可用时执行六条真实转换路径。

## 演示文件

`examples/input/` 包含 TXT、Markdown 和 DOCX 输入样例；`examples/output/` 包含使用当前版本和本机 Pandoc 实际生成的六种路径输出及 JSON 报告，可直接用于作品集演示和结果对照。

## 常见问题

### 页面提示“未找到 Pandoc”

运行：

```bash
brew install pandoc
```

安装后停止并重新启动 Streamlit。可用 `pandoc --version` 验证。

### 中文出现乱码

报告中会记录检测到的编码。工具优先识别 UTF-8，再尝试 charset-normalizer 和 GB18030。若极短文本或混合编码仍判断错误，请用文本编辑器另存为 UTF-8 后重试。

### 为什么不能写到 `/tmp` 或系统目录

网页输入的输出目录仅允许位于当前用户主目录下，以减少路径误输和不可信文件造成的风险。推荐使用 `~/Documents/文档转换输出` 或项目内的 `converted/`。

## 项目结构

```text
app.py                         Streamlit 页面
src/local_doc_converter/       分层转换核心
tests/                          自动化测试
docs/designs/                   分项设计文档
examples/input/                 演示输入
examples/output/                实际转换示例与报告
scripts/start.sh                macOS 启动脚本
设计文档.md                     架构、风险与实施说明
```

更详细的模块边界、安全策略和取舍见 [设计文档.md](设计文档.md)；DOCX 预检的阈值、失败行为和测试策略见 [DOCX 安全预检设计文档](docs/designs/2026-09-22-docx-safety-preflight.md)。

## 许可证

本项目使用 [MIT License](LICENSE)。
