from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.actions.plugin_action import PluginAction, register_action
from typing import Tuple

logger = get_logger("group_whole_ban_action")


@register_action
class GroupWholeBanAction(PluginAction):
    """群聊全体禁言动作处理类"""

    action_name = "group_whole_ban_action"
    action_description = "开启或关闭群聊全体禁言，当群聊过于混乱或需要安静时使用"
    action_parameters = {
        "enable": "是否开启全体禁言，输入True开启，False关闭，必填",
    }
    action_require = [
        "当群聊过于混乱需要安静时使用",
        "当需要临时暂停群聊讨论时使用",
        "当有人要求开启全体禁言时使用",
        "当管理员需要发布重要公告时使用",
    ]
    default = False
    associated_types = ["command", "text"]

    async def process(self) -> Tuple[bool, str]:
        """处理群聊全体禁言动作"""
        logger.info(f"{self.log_prefix} 执行全体禁言动作: {self.reasoning}")

        # 获取参数
        enable = self.action_data.get("enable")

        if enable is None:
            error_msg = "全体禁言参数不完整，需要enable参数"
            logger.error(f"{self.log_prefix} {error_msg}")
            return False, error_msg

        # 确保enable是布尔类型
        if isinstance(enable, str):
            if enable.lower() in ["true", "1", "yes", "开启", "是"]:
                enable = True
            elif enable.lower() in ["false", "0", "no", "关闭", "否"]:
                enable = False
            else:
                error_msg = f"无效的enable参数: {enable}，应该是True或False"
                logger.error(f"{self.log_prefix} {error_msg}")
                return False, error_msg

        # 发送表达情绪的消息
        action_text = "开启" if enable else "关闭"
        await self.send_message_by_expressor(f"我要{action_text}全体禁言")

        try:
            # 发送群聊全体禁言命令，按照新格式
            await self.send_message(type="command", data={"name": "GROUP_WHOLE_BAN", "args": {"enable": enable}})

            logger.info(f"{self.log_prefix} 成功{action_text}全体禁言")
            return True, f"成功{action_text}全体禁言"

        except Exception as e:
            logger.error(f"{self.log_prefix} 执行全体禁言动作时出错: {e}")
            await self.send_message_by_expressor(f"执行全体禁言动作时出错: {e}")
            return False, f"执行全体禁言动作时出错: {e}"
