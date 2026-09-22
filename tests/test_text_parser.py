from local_doc_converter.text_parser import parse_txt_structure


def test_detects_chinese_and_decimal_headings():
    result = parse_txt_structure("第一章 开始\n\n正文。\n\n1.1 背景\n\n内容。")
    assert "# 第一章 开始" in result.markdown
    assert "### 1.1 背景" in result.markdown


def test_detects_tab_table_and_indented_code():
    result = parse_txt_structure("名称\t数量\n苹果\t2\n\n    print('你好')")
    assert "| 名称 | 数量 |" in result.markdown
    assert "| --- | --- |" in result.markdown
    assert "```\nprint('你好')\n```" in result.markdown


def test_short_title_adds_warning():
    result = parse_txt_structure("项目概述\n\n这是一段带有句号的正文。")
    assert "## 项目概述" in result.markdown
    assert result.warnings


def test_bullets_remain_lists():
    result = parse_txt_structure("- 第一项\n- 第二项")
    assert "- 第一项" in result.markdown
    assert "- 第二项" in result.markdown


def test_adjacent_chinese_enumeration_is_one_list():
    result = parse_txt_structure("一、准备材料\n二、开始转换\n三、检查结果")
    assert result.markdown.count("1. ") == 3
    assert "##" not in result.markdown
