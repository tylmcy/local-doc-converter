from local_doc_converter.pdf.quality import assess_native_text


def test_accepts_normal_chinese_and_english_text():
    result = assess_native_text("第一章 本地 PDF 文字提取\n这是一段正常的中文和 English 123。")

    assert result.acceptable
    assert result.score == 1.0


def test_rejects_empty_replacement_private_use_and_repeated_glyphs():
    assert not assess_native_text("  \n").acceptable
    assert not assess_native_text("�" * 10 + "文字").acceptable
    assert not assess_native_text("\ue000" * 10 + "文字").acceptable
    repeated = assess_native_text("技" * 30 + "文字")
    assert not repeated.acceptable
    assert any("高频重复" in warning for warning in repeated.warnings)
