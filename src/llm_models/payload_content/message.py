from enum import Enum


# 设计这系列类的目的是为未来可能的扩展做准备


class RoleType(Enum):
    System = "system"
    User = "user"
    Assistant = "assistant"
    Tool = "tool"


SUPPORTED_IMAGE_FORMATS = ["jpg", "jpeg", "png", "webp", "gif"]  # openai支持的图片格式


class Message:
    def __init__(
        self,
        role: RoleType,
        content: str | list[tuple[str, str] | str],
        tool_call_id: str | None = None,
    ):
        """
        初始化消息对象
        （不应直接修改Message类，而应使用MessageBuilder类来构建对象）
        """
        self.role: RoleType = role
        self.content: str | list[tuple[str, str] | str] = content
        self.tool_call_id: str | None = tool_call_id


class MessageBuilder:
    def __init__(self):
        self.__role: RoleType = RoleType.User
        self.__content: list[tuple[str, str] | str] = []
        self.__tool_call_id: str | None = None

    def set_role(self, role: RoleType = RoleType.User) -> "MessageBuilder":
        """
        设置角色（默认为User）
        :param role: 角色
        :return: MessageBuilder对象
        """
        self.__role = role
        return self

    def add_text_content(self, text: str) -> "MessageBuilder":
        """
        添加文本内容
        :param text: 文本内容
        :return: MessageBuilder对象
        """
        self.__content.append(text)
        return self

    def add_image_content(
        self,
        image_format: str,
        image_base64: str,
        support_formats: list[str] = SUPPORTED_IMAGE_FORMATS,  # 默认支持格式
    ) -> "MessageBuilder":
        """
        添加图片内容
        :param image_format: 图片格式
        :param image_base64: 图片的base64编码
        :return: MessageBuilder对象
        """
        if image_format.lower() not in support_formats:
            raise ValueError("不受支持的图片格式")
        if not image_base64:
            raise ValueError("图片的base64编码不能为空")
        self.__content.append((image_format, image_base64))
        return self

    def add_tool_call(self, tool_call_id: str) -> "MessageBuilder":
        """
        添加工具调用指令（调用时请确保已设置为Tool角色）
        :param tool_call_id: 工具调用指令的id
        :return: MessageBuilder对象
        """
        if self.__role != RoleType.Tool:
            raise ValueError("仅当角色为Tool时才能添加工具调用ID")
        if not tool_call_id:
            raise ValueError("工具调用ID不能为空")
        self.__tool_call_id = tool_call_id
        return self

    def build(self) -> Message:
        """
        构建消息对象
        :return: Message对象
        """
        if len(self.__content) == 0:
            raise ValueError("内容不能为空")
        if self.__role == RoleType.Tool and self.__tool_call_id is None:
            raise ValueError("Tool角色的工具调用ID不能为空")

        return Message(
            role=self.__role,
            content=(
                self.__content[0]
                if (len(self.__content) == 1 and isinstance(self.__content[0], str))
                else self.__content
            ),
            tool_call_id=self.__tool_call_id,
        )
