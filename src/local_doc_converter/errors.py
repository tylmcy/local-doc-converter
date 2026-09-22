"""对用户友好的业务异常。"""


class ConverterError(Exception):
    """所有可预期转换错误的基类。"""


class ValidationError(ConverterError):
    """输入、路径或格式不符合约束。"""


class PandocNotFoundError(ConverterError):
    """本机未安装 Pandoc。"""


class PandocExecutionError(ConverterError):
    """Pandoc 返回失败。"""


class EncodingDetectionError(ConverterError):
    """文本编码无法可靠解码。"""
