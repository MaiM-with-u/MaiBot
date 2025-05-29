from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.actions.plugin_action import PluginAction, register_action
from typing import Tuple

logger = get_logger("mute_action")


@register_action
class MuteAction(PluginAction):
    """群聊禁言动作处理类"""

    action_name = "mute_action"
    action_description = "如果某人违反了公序良俗，或者别人戳你太多，或者某人刷屏，一定要禁言某人，如果你很生气，可以禁言某人，可以自选禁言时长，视严重程度而定。"
    action_parameters = {
        "target": "禁言对象，必填,输入你要禁言的对象的名字",
        "duration": "禁言时长，必填，输入你要禁言的时长（秒），单位为秒，必须为数字",
        "reason": "禁言理由，可选",
    }
    action_require = [
        "当有人违反了公序良俗时使用",
        "当有人刷屏时使用",
        "当有人要求禁言自己时使用",
        "当有人戳你两次以上时，防止刷屏，禁言他，必须牢记",
        "当你想回避某个话题时使用",
    ]
    default = True  # 默认动作，是否手动添加到使用集
    associated_types = ["command", "text"]
    # associated_types = ["text"]

    async def process(self) -> Tuple[bool, str]:
        """处理群聊禁言动作"""
        logger.info(f"{self.log_prefix} 执行禁言动作: {self.reasoning}")

        # 获取参数
        target = self.action_data.get("target")
        duration = self.action_data.get("duration")
        reason = self.action_data.get("reason", "违反群规")

        if not target or not duration:
            error_msg = "禁言参数不完整，需要target和duration"
            logger.error(f"{self.log_prefix} {error_msg}")
            return False, error_msg

        # 获取用户ID
        platform, user_id = await self.get_user_id_by_person_name(target)

        if not user_id:
            error_msg = f"未找到用户 {target} 的ID"
            await self.send_message_by_expressor(f"压根没 {target} 这个人")
            logger.error(f"{self.log_prefix} {error_msg}")
            return False, error_msg

        # 发送表达情绪的消息
        await self.send_message_by_expressor(f"禁言{target} {duration}秒，因为{reason}")

        try:
            # 确保duration是字符串类型
            if int(duration) < 60:
                duration = 60
            if int(duration) > 3600 * 24 * 30:
                duration = 3600 * 24 * 30
            duration_str = str(int(duration))

            # 发送群聊禁言命令，按照新格式
            await self.send_message(
                type="command",
                data={"name": "GROUP_BAN", "args": {"qq_id": str(user_id), "duration": duration_str}},
                display_message=f"我 禁言了 {target} {duration_str}秒",
            )

            logger.info(f"{self.log_prefix} 成功发送禁言命令，用户 {target}({user_id})，时长 {duration} 秒")
            return True, f"成功禁言 {target}，时长 {duration} 秒"

        except Exception as e:
            logger.error(f"{self.log_prefix} 执行禁言动作时出错: {e}")
            await self.send_message_by_expressor(f"执行禁言动作时出错: {e}")
            return False, f"执行禁言动作时出错: {e}"
