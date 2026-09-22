"""Streamlit 本地网页入口。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from local_doc_converter.batch import BatchProcessor
from local_doc_converter.config import DEFAULT_OUTPUT_DIR, SUPPORTED_EXTENSIONS, TARGET_LABELS
from local_doc_converter.errors import ConverterError
from local_doc_converter.models import UploadedDocument
from local_doc_converter.pandoc import PandocRunner
from local_doc_converter.platform_tools import open_directory
from local_doc_converter.security import ensure_output_dir


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _reset_output_dir() -> None:
    st.session_state["output_dir"] = str(DEFAULT_OUTPUT_DIR)


def _start_new_batch() -> None:
    st.session_state.pop("batch_result", None)
    st.session_state.pop("result_output_dir", None)
    st.session_state["upload_generation"] = st.session_state.get("upload_generation", 0) + 1


st.set_page_config(page_title="本地文档互转", page_icon="📄", layout="wide")
st.session_state.setdefault("upload_generation", 0)
st.session_state.setdefault("output_dir", str(DEFAULT_OUTPUT_DIR))

title_col, version_col = st.columns([5, 1])
with title_col:
    st.title("📄 本地离线文档互转")
    st.caption("TXT、Markdown 与 DOCX 批量互转 · 文件仅在本机处理 · 不调用 AI 或在线服务")
with version_col:
    st.markdown("##### 稳定版本")
    st.code("v1.0.0", language=None)

pandoc = PandocRunner()
if pandoc.available:
    try:
        st.success(f"转换引擎就绪：{pandoc.version()}", icon="✅")
    except ConverterError as exc:
        st.error(str(exc))
else:
    st.error("未找到 Pandoc。请先在 macOS 终端运行 `brew install pandoc`，然后重新启动本应用。")

with st.expander("使用说明与隐私说明"):
    st.markdown(
        """
1. 上传一个或多个 TXT、Markdown 或 DOCX 文档。
2. 为整个批次选择目标格式，并确认本地输出目录。
3. 转换完成后可下载单个文件或包含报告的 ZIP。

上传内容只会进入本机临时目录，任务结束后立即清理。程序不会上传文件、调用模型 API，
也不会主动下载 Markdown 中的远程图片。同名结果会自动添加数字后缀，不覆盖原文件。
        """
    )

st.subheader("1. 选择文件和目标格式")
upload_col, target_col = st.columns([3, 1])
with upload_col:
    uploaded_files = st.file_uploader(
        "拖入或选择一个或多个文档",
        type=["txt", "md", "markdown", "docx"],
        accept_multiple_files=True,
        help="单文件最大 50 MB，单批最多 100 个文件、总计 200 MB。",
        key=f"uploads_{st.session_state['upload_generation']}",
    )
with target_col:
    target_format = st.selectbox(
        "统一转换为",
        options=list(TARGET_LABELS),
        format_func=lambda value: TARGET_LABELS[value],
    )
    st.caption("同格式文件不会重复转换。")

same_format_files: list[str] = []
if uploaded_files:
    preview_rows = []
    for uploaded in uploaded_files:
        source_format = SUPPORTED_EXTENSIONS.get(Path(uploaded.name).suffix.lower(), "unknown")
        same_format = source_format == target_format
        if same_format:
            same_format_files.append(uploaded.name)
        preview_rows.append(
            {
                "文件名": uploaded.name,
                "源格式": TARGET_LABELS.get(source_format, source_format),
                "大小": _format_size(uploaded.size),
                "处理状态": "源格式与目标格式相同" if same_format else "等待转换",
            }
        )
    st.dataframe(preview_rows, use_container_width=True, hide_index=True)
    if same_format_files:
        st.warning(
            f"有 {len(same_format_files)} 个文件已经是 {TARGET_LABELS[target_format]}，"
            "它们会生成说明报告，不会重复转换：" + "、".join(same_format_files)
        )

st.subheader("2. 确认本地输出目录")
directory_col, reset_col = st.columns([5, 1])
with directory_col:
    output_dir = st.text_input(
        "输出目录",
        key="output_dir",
        help="目录必须位于当前用户主目录下；不存在时会在开始转换后自动创建。",
        label_visibility="collapsed",
    )
with reset_col:
    st.button("恢复默认目录", on_click=_reset_output_dir, use_container_width=True)
st.caption("已有同名文件不会被覆盖，程序会自动添加 `_2`、`_3` 等后缀。")

st.subheader("3. 开始转换")
start_disabled = not uploaded_files or not pandoc.available
if st.button("开始转换", type="primary", disabled=start_disabled, use_container_width=True):
    documents = [UploadedDocument(name=file.name, data=file.getvalue()) for file in uploaded_files]
    try:
        # 在批处理前统一验证，避免转换完才发现输出目录不可用。
        resolved_output = ensure_output_dir(Path(output_dir))
        progress = st.progress(0.0, text="正在准备转换……")

        def update_progress(completed: int, total: int, filename: str) -> None:
            progress.progress(completed / total, text=f"正在处理 {completed}/{total}：{filename}")

        batch_result = BatchProcessor().process_uploads(
            documents,
            target_format,
            resolved_output,
            on_progress=update_progress,
        )
        progress.progress(1.0, text="转换完成")
        st.session_state["batch_result"] = batch_result
        st.session_state["result_output_dir"] = str(resolved_output)
    except (ConverterError, OSError) as exc:
        st.error(f"无法开始转换：{exc}")

batch_result = st.session_state.get("batch_result")
if batch_result:
    st.divider()
    st.subheader("转换结果")
    warning_count = sum(
        len(result.report.warnings) + len(result.report.possible_losses)
        for result in batch_result.results
    )
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("文件总数", len(batch_result.results))
    col2.metric("成功", batch_result.successful_count)
    col3.metric("已跳过", batch_result.skipped_count)
    col4.metric("失败", batch_result.failed_count)
    col5.metric("提示与警告", warning_count)

    if batch_result.failed_count:
        st.warning("批次已完成，但有文件未成功转换。请在“详细报告”中查看原因。")
    elif batch_result.skipped_count:
        st.info("批次已完成；与目标格式相同的文件已跳过，其余文件转换成功。")
    else:
        st.success("本批次已全部处理完成。")

    action_download, action_open, action_reset = st.columns(3)
    with action_download:
        st.download_button(
            "下载全部结果和报告（ZIP）",
            data=batch_result.zip_bytes,
            file_name=batch_result.zip_name,
            mime="application/zip",
            type="primary",
            use_container_width=True,
        )
    with action_open:
        if st.button("在访达中打开输出目录", use_container_width=True):
            try:
                open_directory(Path(st.session_state["result_output_dir"]))
            except (ConverterError, OSError) as exc:
                st.error(str(exc))
    with action_reset:
        st.button("开始新批次", on_click=_start_new_batch, use_container_width=True)

    result_output_dir = st.session_state.get("result_output_dir")
    if result_output_dir:
        st.caption(f"结果已保存到：{result_output_dir}")

    overview_tab, detail_tab = st.tabs(["结果概览", "详细报告与单文件下载"])
    with overview_tab:
        rows = []
        for result in batch_result.results:
            report = result.report
            rows.append(
                {
                    "源文件": report.source_file,
                    "状态": "成功" if report.success else ("已跳过" if report.skipped else "失败"),
                    "输出文件": report.target_file or "—",
                    "编码": report.detected_encoding or "—",
                    "标题": report.stats.headings,
                    "列表": report.stats.lists,
                    "表格": report.stats.tables,
                    "代码块": report.stats.code_blocks,
                    "链接": report.stats.links,
                    "图片": report.stats.images,
                    "耗时（秒）": report.duration_seconds,
                    "错误": report.error or "",
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)

    with detail_tab:
        for result in batch_result.results:
            report = result.report
            status_icon = "✅" if report.success else ("⏭️" if report.skipped else "❌")
            with st.expander(f"{status_icon} {report.source_file}"):
                if report.target_file:
                    st.markdown(f"**输出文件：** `{report.target_file}`")
                if report.warnings:
                    st.warning("\n".join(f"• {item}" for item in report.warnings))
                if report.possible_losses:
                    st.info("\n".join(f"• {item}" for item in report.possible_losses))
                if report.error:
                    st.error(report.error)
                if result.asset_paths:
                    st.caption("该 Markdown 包含提取的图片资源，移动文件时请同时使用 ZIP 中的资源目录。")
                if result.output_path and result.output_path.exists():
                    st.download_button(
                        "下载该文件",
                        data=result.output_path.read_bytes(),
                        file_name=result.output_path.name,
                        key=f"download_{result.output_path}",
                    )

st.divider()
st.caption("Local Document Converter v1.0.0 · 本地离线运行 · 当前支持 TXT / Markdown / DOCX")
