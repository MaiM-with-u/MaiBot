from src.tools.tool_can_use.base_tool import BaseTool
from src.person_info.person_info import person_info_manager
from src.common.logger_manager import get_logger
import time

logger = get_logger("rename_person_tool")


class RenamePersonTool(BaseTool):
    name = "rename_person"
    description = (
        "This tool can change user nicknames. You can choose to change how you address others. If you want to rename someone or call them by a different name, you need to call this tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "person_name": {"type": "string", "description": "Current nickname of the user who needs to be renamed"},
            "message_content": {
                "type": "string",
                "description": "Current chat content or specific requirements, used to provide context for naming suggestions, as detailed as possible.",
            },
        },
        "required": ["person_name"],
    }

    async def execute(self, function_args: dict, message_txt=""):
        """
        执行取名工具逻辑

        Args:
            function_args (dict): 包含 'person_name' 和可选 'message_content' 的字典
            message_txt (str): 原始消息文本 (这里未使用，因为 message_content 更明确)

        Returns:
            dict: 包含执行结果的字典
        """
        person_name_to_find = function_args.get("person_name")
        request_context = function_args.get("message_content", "")  # 如果没有提供，则为空字符串

        if not person_name_to_find:
            return {"name": self.name, "content": "Error: Must provide the nickname of the user to be renamed (person_name)."}

        try:
            # 1. 根据昵称查找用户信息
            logger.debug(f"尝试根据昵称 '{person_name_to_find}' 查找用户...")
            person_info = await person_info_manager.get_person_info_by_name(person_name_to_find)

            if not person_info:
                logger.info(f"未找到昵称为 '{person_name_to_find}' 的用户。")
                return {
                    "name": self.name,
                    "content": f"Cannot find user with nickname '{person_name_to_find}'. Please ensure you entered the nickname I previously gave to that user.",
                }

            person_id = person_info.get("person_id")
            user_nickname = person_info.get("nickname")  # 这是用户原始昵称
            user_cardname = person_info.get("user_cardname")
            user_avatar = person_info.get("user_avatar")

            if not person_id:
                logger.error(f"Found user '{person_name_to_find}' but unable to get person_id")
                return {"name": self.name, "content": f"Found user '{person_name_to_find}' but error occurred while getting internal ID."}

            # 2. 调用 qv_person_name 进行取名
            logger.debug(
                f"为用户 {person_id} (原昵称: {person_name_to_find}) 调用 qv_person_name，请求上下文: '{request_context}'"
            )
            result = await person_info_manager.qv_person_name(
                person_id=person_id,
                user_nickname=user_nickname,
                user_cardname=user_cardname,
                user_avatar=user_avatar,
                request=request_context,
            )

            # 3. 处理结果
            if result and result.get("nickname"):
                new_name = result["nickname"]
                # reason = result.get("reason", "未提供理由")
                logger.info(f"成功为用户 {person_id} 取了新昵称: {new_name}")

                content = f"Successfully updated user {person_name_to_find}'s nickname to {new_name}"
                logger.info(content)
                return {"type": "info", "id": f"rename_success_{time.time()}", "content": content}
            else:
                logger.warning(f"为用户 {person_id} 调用 qv_person_name 后未能成功获取新昵称。")
                # 尝试从内存中获取可能已经更新的名字
                current_name = await person_info_manager.get_value(person_id, "person_name")
                if current_name and current_name != person_name_to_find:
                    return {
                        "name": self.name,
                        "content": f"Encountered a small issue while trying to create a new nickname, but I have already updated '{person_name_to_find}''s nickname to '{current_name}'.",
                    }
                else:
                    return {
                        "name": self.name,
                        "content": f"Encountered a problem while trying to create a new nickname for '{person_name_to_find}', failed to generate successfully. May need to try again later.",
                    }

        except Exception as e:
            error_msg = f"Rename failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {"type": "info_error", "id": f"rename_error_{time.time()}", "content": error_msg}


# 注册工具
#register_tool(RenamePersonTool)
