from local_doc_converter.encoding import decode_text


def test_decode_utf8_chinese():
    result = decode_text("中文测试".encode("utf-8"))
    assert result.text == "中文测试"
    assert result.encoding in {"utf-8", "utf-8-sig"}


def test_decode_gbk_chinese():
    result = decode_text("第一章 中文编码".encode("gbk"))
    assert result.text == "第一章 中文编码"
    assert result.encoding.lower().replace("_", "-") in {"gbk", "gb18030", "cp936"}
