# 真实 PDF 质量样例集

这个目录用于验收 PDF → TXT 的阅读顺序、表格结构、旋转页和 OCR 回退质量。它不是只收集“已经通过”的演示文件，已知失败样例同样必须保留，以便后续修复不发生回归。

## 目录与发布边界

- `manifest.json`：可提交，记录固定来源版本、SHA-256、文件大小、场景标签、自动验收阈值和人工复核结论。
- `downloads/`：仅保留在本机，Git 忽略。第三方 PDF 不随项目再分发。
- `results/`：每次评估创建独立时间戳目录，保存 TXT、转换报告和 `quality_report.json`，Git 忽略。
- `renders/`：人工视觉复核用的页面 PNG，Git 忽略。
- `local-manifest.json`：用于个人或含隐私样例，Git 忽略。不应在可提交的清单中写入本机绝对路径、用户名、邮箱或业务信息。
- `generated/` 与 `scanned_table_truth.json`：项目自行生成的中文扫描表格及标准答案，可随代码发布；不含第三方 PDF 或文字层。

即使上游代码仓库使用 MIT/Apache-2.0，其测试 PDF 也可能来自其他来源。因此首批全部标记为 `local-only`，仅保存来源页和仓库许可说明，不把 PDF 二进制文件推送到 GitHub。

## 下载

下载是显式操作，需要联网；日常转换仍保持离线。

```bash
uv run python scripts/download_pdf_corpus.py
```

只下载一个样例：

```bash
uv run python scripts/download_pdf_corpus.py \
  --sample camelot-row-span
```

下载器会限制单文件不超过 50 MB，并校验 HTTPS、安全文件名、PDF 文件头、精确字节数和 SHA-256。已存在但哈希不一致的文件不会被静默覆盖。

## 评估

```bash
uv run python scripts/evaluate_pdf_corpus.py
```

自动验收只负责硬性条件：文件完整性、转换成功、页数、PDF 类型、原生/OCR/空白页数量、中日韩统一表意文字与拉丁字母数量、OCR 平均置信度、表格数和指定安全警告。阅读顺序、表格语义和 OCR 错字必须结合页面渲染做人工复核。

验收记录：

- [首批英文基线](../../docs/validation/2026-09-23-pdf-quality-corpus-validation.md)
- [第二批中文、中英混排与真实扫描件](../../docs/validation/2026-09-23-pdf-quality-corpus-second-batch-validation.md)
- [竖排页边清理、高精度识别与现代扫描验收](../../docs/validation/2026-09-23-pdf-ocr-third-batch-validation.md)
- [扫描表格与分辨率对照](../../docs/validation/2026-09-23-scanned-table-dpi-validation.md)
- [V2 九份样例关键页与固定五页阅读顺序复核](../../docs/validation/2026-09-24-v2-pdf-manual-closeout.md)
- [固定五页旧印刷体正式字符评分](../../docs/validation/2026-09-24-old-print-formal-score.md)

## 自制扫描表格与 DPI 对照

仓库内有同一张采购明细表的 150/300 PPI 图像型 PDF。两份都只有位图，没有可提取文字层，因此能验证真正的 OCR 回退。重新生成与对照：

```bash
.venv/bin/python scripts/generate_scanned_table_fixture.py
.venv/bin/python scripts/compare_scanned_table_dpi.py
```

生成脚本会调用本机 `fc-match` 查找中文字体；如果没有，可传 `--font`。对照脚本需要 README 所述本地 PaddleOCR 模型，不联网。它在每次运行的 `results/scanned-table-dpi-*` 中输出 6 份 TXT 和 JSON 指标。单元格召回、项目行序与 Tab 行结构分别统计，不以 OCR 置信度代替真实文字质量。

当前实现只对横竖网格线清楚、至少 3 行多列的扫描表格按单元格坐标恢复 Tab 列；不推断无框表格、倾斜表格、跨页表格、合并单元格。未达到保守门槛时保持普通 OCR 文字输出。

## 英文基线样例

| ID | 场景 | 当前结论 |
|---|---|---|
| `federal-register-three-column` | 15 页联邦公报，三栏、脚注、表格和图片 | 主要三栏正文已按左中右恢复；跨栏密集表格及图片文字仍有损失 |
| `camelot-report-table` | 单栏技术报告与多行表头 | 通过，表头有纯文本降级 |
| `camelot-row-span` | 跨行合并的大型有框表格 | 通过，合并位使用空占位 |
| `camelot-hybrid-multipage` | 两页三列无框表格、PDF 打开动作 | 通过，安全警告正常 |
| `pdfplumber-rotated-table` | 90° 旋转的密集统计表 | 已知失败：表头与数据不可读 |

## 中文与扫描样例

| ID | 场景 | 当前结论 |
|---|---|---|
| `court-simplified-chinese` | 简体中文单栏司法公告、金额、日期和印章 | 通过；印章不输出符合 TXT 边界 |
| `tsinghua-chinese-english-guide` | 40 页中英混排招生指南、双栏和表格 | 第 6–8 页大段跨页重复已去除；表单、公式及局部混排仍需复核 |
| `wikimedia-chinese-scan` | 5 页真实繁体中文竖排扫描、少量英文 | 5/5 页触发 OCR；主要阅读顺序可读；冻结参考上正式正确率 93.14%，达到 90% 门槛；实验增强模式未见明确收益 |
| `yunnan-modern-scan` | 4 页现代简体中文横排扫描公文、印章、长网址 | 4/4 页触发 OCR；正文顺序可读，印章透字和长网址断行仍可能出现 |

当前已覆盖简体中文、中英混排、现代横排和旧式竖排真实扫描 PDF，并有自制扫描表格及分辨率对照。旧式繁体竖排样例已能恢复右到左的主要列序并清理跨页重复边栏标题，但识别错字、漏字和独有边栏文字仍是边界。下一批仍需补充倾斜、模糊和真实扫描表格；在完成这些覆盖前，不宣称中文扫描 PDF 的主要场景已经通过质量验收。

## 人工复核规则

1. 渲染原 PDF 的全部相关页，确认分栏、旋转、表格线和页眉页脚。
2. 对照 TXT，先检查大块阅读顺序，再检查段落和表格行列。
3. `pass`：无影响阅读的错序；`pass-with-loss`：存在 TXT 必然降级但语义可读；`known-issue`：已确认的不可接受问题。
4. 修复后必须重新复核失败样例，同时重跑所有已通过样例。
