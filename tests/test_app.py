from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_app_renders_without_exception():
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py").run(timeout=10)
    assert not app.exception
    assert app.title[0].value == "📄 本地离线文档互转"
    assert [item.value for item in app.subheader] == [
        "1. 选择文件和目标格式",
        "2. 确认本地输出目录",
        "3. 开始转换",
    ]
    assert any(item.value == "v1.0.0" for item in app.code)
