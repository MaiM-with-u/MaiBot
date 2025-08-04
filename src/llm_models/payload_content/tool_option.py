from enum import Enum


class ToolParamType(Enum):
    """
    工具调用参数类型
    """

    STRING = "string"  # 字符串
    INTEGER = "integer"  # 整型
    FLOAT = "float"  # 浮点型
    BOOLEAN = "bool"  # 布尔型


class ToolParam:
    """
    工具调用参数
    """

    def __init__(
        self,
        name: str,
        param_type: ToolParamType,
        description: str,
        required: bool,
        enum_values: list[str] | None = None,
    ):
        """
        初始化工具调用参数
        （不应直接修改ToolParam类，而应使用ToolOptionBuilder类来构建对象）
        :param name: 参数名称
        :param param_type: 参数类型
        :param description: 参数描述
        :param required: 是否必填
        """
        self.name: str = name
        self.param_type: ToolParamType = param_type
        self.description: str = description
        self.required: bool = required
        self.enum_values: list[str] | None = enum_values


class ToolOption:
    """
    工具调用项
    """

    def __init__(
        self,
        name: str,
        description: str,
        params: list[ToolParam] | None = None,
    ):
        """
        初始化工具调用项
        （不应直接修改ToolOption类，而应使用ToolOptionBuilder类来构建对象）
        :param name: 工具名称
        :param description: 工具描述
        :param params: 工具参数列表
        """
        self.name: str = name
        self.description: str = description
        self.params: list[ToolParam] | None = params


class ToolOptionBuilder:
    """
    工具调用项构建器
    """

    def __init__(self):
        self.__name: str = ""
        self.__description: str = ""
        self.__params: list[ToolParam] = []

    def set_name(self, name: str) -> "ToolOptionBuilder":
        """
        设置工具名称
        :param name: 工具名称
        :return: ToolBuilder实例
        """
        if not name:
            raise ValueError("工具名称不能为空")
        self.__name = name
        return self

    def set_description(self, description: str) -> "ToolOptionBuilder":
        """
        设置工具描述
        :param description: 工具描述
        :return: ToolBuilder实例
        """
        if not description:
            raise ValueError("工具描述不能为空")
        self.__description = description
        return self

    def add_param(
        self,
        name: str,
        param_type: ToolParamType,
        description: str,
        required: bool = False,
        enum_values: list[str] | None = None,
    ) -> "ToolOptionBuilder":
        """
        添加工具参数
        :param name: 参数名称
        :param param_type: 参数类型
        :param description: 参数描述
        :param required: 是否必填（默认为False）
        :return: ToolBuilder实例
        """
        if not name or not description:
            raise ValueError("参数名称/描述不能为空")

        self.__params.append(
            ToolParam(
                name=name,
                param_type=param_type,
                description=description,
                required=required,
                enum_values=enum_values,
            )
        )

        return self

    def build(self):
        """
        构建工具调用项
        :return: 工具调用项
        """
        if self.__name == "" or self.__description == "":
            raise ValueError("工具名称/描述不能为空")

        return ToolOption(
            name=self.__name,
            description=self.__description,
            params=None if len(self.__params) == 0 else self.__params,
        )


class ToolCall:
    """
    来自模型反馈的工具调用
    """

    def __init__(
        self,
        call_id: str,
        func_name: str,
        args: dict | None = None,
    ):
        """
        初始化工具调用
        :param call_id: 工具调用ID
        :param func_name: 要调用的函数名称
        :param args: 工具调用参数
        """
        self.call_id: str = call_id
        self.func_name: str = func_name
        self.args: dict | None = args
