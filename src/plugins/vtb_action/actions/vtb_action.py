from src.common.logger_manager import get_logger
from src.chat.focus_chat.planners.actions.plugin_action import PluginAction, register_action
from typing import Tuple

logger = get_logger("vtb_action")


@register_action
class VTBAction(PluginAction):
    """VTB虚拟主播动作处理类"""

    action_name = "vtb_action"
    action_description = "使用虚拟主播预设动作表达心情或感觉，适用于需要生动表达情感的场景"
    action_parameters = {
        "text": "描述想要表达的心情或感觉的文本内容，必填，应当是对情感状态的自然描述",
    }
    action_require = [
        "当需要表达特定情感或心情时使用",
        "当用户明确要求使用虚拟主播动作时使用",
        "当回应内容需要更生动的情感表达时使用",
        "当想要通过预设动作增强互动体验时使用",
    ]
    default = True  # 设为默认动作
    associated_types = ["vtb_text"]

    async def process(self) -> Tuple[bool, str]:
        """处理VTB虚拟主播动作"""
        logger.info(f"{self.log_prefix} 执行VTB动作: {self.reasoning}")

        # 获取要表达的心情或感觉文本
        text = self.action_data.get("text")

        if not text:
            logger.error(f"{self.log_prefix} 执行VTB动作时未提供文本内容")
            return False, "执行VTB动作失败：未提供文本内容"

        # 处理文本使其更适合VTB动作表达
        processed_text = self._process_text_for_vtb(text)

        try:
            # 发送VTB动作消息
            await self.send_message(type="vtb_text", data=processed_text)

            logger.info(f"{self.log_prefix} VTB动作执行成功，文本内容: {processed_text}")
            return True, "VTB动作执行成功"

        except Exception as e:
            logger.error(f"{self.log_prefix} 执行VTB动作时出错: {e}")
            return False, f"执行VTB动作时出错: {e}"

    def _process_text_for_vtb(self, text: str) -> str:
        """
        处理文本使其更适合VTB动作表达
        - 优化情感表达的准确性
        - 规范化心情描述格式
        - 确保文本适合虚拟主播动作系统理解
        """
        # 简单示例实现
        processed_text = text.strip()

        # 移除多余的空格和换行
        import re

        processed_text = re.sub(r"\s+", " ", processed_text)

        # 确保文本长度适中，避免过长的描述
        if len(processed_text) > 100:
            processed_text = processed_text[:100] + "..."

        # 如果文本为空，提供默认的情感描述
        if not processed_text:
            processed_text = "平静"

        return processed_text
