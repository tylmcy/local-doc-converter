# DOCX 输入安全预检规格

## Purpose

记录 `v1.0.1` 已发布的 DOCX ZIP 容器预检行为。它是进入 Pandoc 前的资源与路径安全边界，不承担病毒检测或 Word 文档语义解析。

## Requirements

### Requirement: 容器结构与资源限额

DOCX 输入 SHALL 在 Pandoc 读取前接受只读、流式 ZIP 预检。容器须包含 `[Content_Types].xml` 与 `word/document.xml`；条目最多 5,000 个、总解压大小最多 500 MB、单条目解压大小最多 100 MB、总压缩比最多 200 倍、单条目压缩比最多 1,000 倍。超限或 CRC 损坏 SHALL 拒绝转换，不生成成功输出。

#### Scenario: 异常压缩 DOCX
- **WHEN** DOCX 的 ZIP 条目数、解压大小或压缩比超出相应上限
- **THEN** 文件在进入 Pandoc 前被拒绝，报告给出可理解的失败原因

### Requirement: ZIP 条目安全

预检 SHALL 拒绝绝对路径、路径穿越、反斜杠路径、重复条目、符号链接和加密条目；不得将 DOCX 容器解压到磁盘以完成预检。

#### Scenario: 路径穿越条目
- **WHEN** DOCX ZIP 包含 `../` 等不安全条目名
- **THEN** 预检拒绝该文件，且不写出条目内容

### Requirement: 活动内容只警告

预检发现宏项目、OLE 嵌入对象或外部关系时 SHALL 在报告中警告，但不执行、访问或自动删除这些内容。此预检 SHALL 不宣称可检测病毒或恶意正文内容。

#### Scenario: 带宏或外部关系的 DOCX
- **WHEN** DOCX 容器结构安全但包含宏、OLE 或外部关系
- **THEN** 预检产生相应警告，允许后续正常转换流程继续
