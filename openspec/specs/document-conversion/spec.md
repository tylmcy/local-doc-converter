# 文档转换规格

## Purpose

记录 `v1.0.1` 已发布并经测试覆盖的 TXT、Markdown、DOCX 转换行为，作为后续格式增量的兼容基线。事实依据为 Git 标签 `v1.0.1` 的 README、设计文档、实现与测试；本规格不把未提交的 PDF 开发代码追认为 V1。

## Requirements

### Requirement: 六条现有转换路径

系统 SHALL 支持 TXT → Markdown、TXT → DOCX、Markdown → TXT、Markdown → DOCX、DOCX → Markdown、DOCX → TXT，输出文件使用目标格式的标准扩展名。系统 SHALL 不修改输入文件。

#### Scenario: 六条路径端到端转换
- **WHEN** 用户为 TXT、Markdown 或 DOCX 输入选择另一种已支持的目标格式
- **THEN** 对应转换生成非空输出文件，并保持原输入文件内容不变

### Requirement: 中文文本编码与结构

系统 SHALL 优先正确读取 UTF-8，并识别常见 GBK、GB18030 文本编码。TXT 输入 SHALL 通过离线规则识别常见中文章节、数字层级标题、列表、简单表格和缩进代码块；低置信度的短行标题推断 SHALL 写入警告。转换 SHALL 尽可能保留可表达的标题、段落、列表、表格、代码块和链接语义，但不承诺版式无损。

#### Scenario: 中文 TXT 转为结构化文档
- **WHEN** TXT 含有“第一章”、数字标题、普通段落和项目列表
- **THEN** 转换结果表达这些基本结构，报告记录可识别的结构数量与必要的推断警告

#### Scenario: Markdown 软换行转 TXT
- **WHEN** Markdown 正文同一段内有软换行，且后面另有段落
- **THEN** TXT 保留段内换行以及段落间的空行

### Requirement: 本地图片与离线边界

系统 SHALL 不主动下载远程图片。CLI 转换 Markdown 时 MAY 读取文档同目录的本地图片；DOCX 转 Markdown 时 SHALL 将可提取媒体放入输出旁的资源目录，并让媒体文件进入下载包。无法安全读取或不存在的图片 SHALL 降级或警告，不得因此访问网络。

#### Scenario: DOCX 内嵌图片转 Markdown
- **WHEN** DOCX 含有 Pandoc 可提取的内嵌图片
- **THEN** 输出 Markdown 与其本地资源目录可一同交付

#### Scenario: 远程图片引用
- **WHEN** 输入文档引用网络图片
- **THEN** 转换过程中不请求该网络地址，并将不可嵌入的图片安全降级或提示

### Requirement: 转换引擎缺失提示

六条 V1 路径依赖本机 Pandoc。系统 SHALL 在 Pandoc 不可用时给出可理解的错误或安装提示，而不是声称转换成功。

#### Scenario: Pandoc 未安装
- **WHEN** 用户请求 TXT、Markdown 或 DOCX 之间的转换，且本机无 Pandoc
- **THEN** 转换失败或入口被禁用，并提示适用于 macOS 的安装方式
