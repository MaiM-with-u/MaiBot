from src.common.logger_manager import get_logger
from src.chat.actions.plugin_action import PluginAction, register_action, ActionActivationType
from src.chat.actions.base_action import ChatMode
from typing import Tuple

logger = get_logger("mute_action")


@register_action
class MuteAction(PluginAction):
    """群聊禁言动作处理类"""

    action_name = "mute_action"
    action_description = "在特定情境下，对某人采取禁言，让他不能说话"
    action_parameters = {
        "target": "禁言对象，必填,输入你要禁言的对象的名字",
        "duration": "禁言时长，必填，输入你要禁言的时长（秒），单位为秒，必须为数字",
        "reason": "禁言理由，可选",
    }
    action_require = [
        "当有人违反了公序良俗的内容",
        "当有人刷屏时使用",
        "当有人发了擦边，或者色情内容时使用",
        "当有人要求禁言自己时使用",
        "如果某人已经被禁言了，就不要再次禁言了，除非你想追加时间！！"
    ]
    enable_plugin = True  # 启用插件
    associated_types = ["command", "text"]
    action_config_file_name = "mute_action_config.toml"
    
    # 激活类型设置
    focus_activation_type = ActionActivationType.LLM_JUDGE  # Focus模式使用LLM判定，确保谨慎
    normal_activation_type = ActionActivationType.KEYWORD   # Normal模式使用关键词激活，快速响应
    
    
    # 关键词设置（用于Normal模式）
    activation_keywords = ["禁言", "mute", "ban", "silence"]
    keyword_case_sensitive = False
    
    # LLM判定提示词（用于Focus模式）
    llm_judge_prompt = """
判定是否需要使用禁言动作的严格条件：

必须使用禁言的情况：
1. 用户发送明显违规内容（色情、暴力、政治敏感等）
2. 恶意刷屏或垃圾信息轰炸
3. 用户主动明确要求被禁言（"禁言我"等）
4. 严重违反群规的行为
5. 恶意攻击他人或群组管理

绝对不要使用的情况：
1. 正常聊天和讨论，即使话题敏感
2. 情绪化表达但无恶意
3. 开玩笑或调侃，除非过分
4. 单纯的意见分歧或争论
5. 轻微的不当言论（应优先提醒）
6. 用户只是提到"禁言"词汇但非要求

注意：禁言是严厉措施，只在明确违规或用户主动要求时使用。
宁可保守也不要误判，保护用户的发言权利。
"""
    
    # Random激活概率（备用）
    random_activation_probability = 0.05  # 设置很低的概率作为兜底

    # 模式启用设置 - 禁言功能在所有模式下都可用
    mode_enable = ChatMode.ALL
    
    # 并行执行设置 - 禁言动作可以与回复并行执行，不覆盖回复内容
    parallel_action = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 生成配置文件（如果不存在）
        self._generate_config_if_needed()
    
    def _generate_config_if_needed(self):
        """生成配置文件（如果不存在）"""
        import os
        
        # 获取动作文件所在目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(current_dir, "mute_action_config.toml")
        
        if not os.path.exists(config_path):
            config_content = """\
# 禁言动作配置文件

# 默认禁言时长限制（秒）
min_duration = 60           # 最短禁言时长
max_duration = 2592000      # 最长禁言时长（30天）
default_duration = 300      # 默认禁言时长（5分钟）

# 禁言消息模板
templates = [
    "好的，禁言 {target} {duration}，理由：{reason}",
    "收到，对 {target} 执行禁言 {duration}，因为{reason}",
    "明白了，禁言 {target} {duration}，原因是{reason}"
]

# 错误消息模板
error_messages = [
    "没有指定禁言对象呢~",
    "没有指定禁言时长呢~", 
    "禁言时长必须是正数哦~",
    "禁言时长必须是数字哦~",
    "找不到 {target} 这个人呢~",
    "查找用户信息时出现问题~"
]

# 是否启用时长美化显示
enable_duration_formatting = true

# 是否记录禁言历史
log_mute_history = true
"""
            try:
                with open(config_path, "w", encoding="utf-8") as f:
                    f.write(config_content)
                logger.info(f"已生成禁言动作配置文件: {config_path}")
            except Exception as e:
                logger.error(f"生成配置文件失败: {e}")

    def _get_duration_limits(self) -> tuple[int, int, int]:
        """获取时长限制配置"""
        min_dur = self.config.get("min_duration", 60)
        max_dur = self.config.get("max_duration", 2592000)
        default_dur = self.config.get("default_duration", 300)
        return min_dur, max_dur, default_dur

    def _get_template_message(self, target: str, duration_str: str, reason: str) -> str:
        """获取模板化的禁言消息"""
        templates = self.config.get("templates", [
            "好的，禁言 {target} {duration}，理由：{reason}"
        ])
        
        import random
        template = random.choice(templates)
        return template.format(target=target, duration=duration_str, reason=reason)

    async def process(self) -> Tuple[bool, str]:
        """处理群聊禁言动作"""
        logger.info(f"{self.log_prefix} 执行禁言动作: {self.reasoning}")

        # 获取参数
        target = self.action_data.get("target")
        duration = self.action_data.get("duration")
        reason = self.action_data.get("reason", "违反群规")

        # 参数验证
        if not target:
            error_msg = "禁言目标不能为空"
            logger.error(f"{self.log_prefix} {error_msg}")
            await self.send_message_by_expressor("没有指定禁言对象呢~")
            return False, error_msg

        if not duration:
            error_msg = "禁言时长不能为空"
            logger.error(f"{self.log_prefix} {error_msg}")
            await self.send_message_by_expressor("没有指定禁言时长呢~")
            return False, error_msg

        # 获取时长限制配置
        min_duration, max_duration, default_duration = self._get_duration_limits()
        
        # 验证时长格式并转换
        try:
            duration_int = int(duration)
            if duration_int <= 0:
                error_msg = "禁言时长必须大于0"
                logger.error(f"{self.log_prefix} {error_msg}")
                error_templates = self.config.get("error_messages", ["禁言时长必须是正数哦~"])
                await self.send_message_by_expressor(error_templates[2] if len(error_templates) > 2 else "禁言时长必须是正数哦~")
                return False, error_msg
                
            # 限制禁言时长范围
            if duration_int < min_duration:
                duration_int = min_duration
                logger.info(f"{self.log_prefix} 禁言时长过短，调整为{min_duration}秒")
            elif duration_int > max_duration:
                duration_int = max_duration
                logger.info(f"{self.log_prefix} 禁言时长过长，调整为{max_duration}秒")
                
        except (ValueError, TypeError) as e:
            error_msg = f"禁言时长格式无效: {duration}"
            logger.error(f"{self.log_prefix} {error_msg}")
            error_templates = self.config.get("error_messages", ["禁言时长必须是数字哦~"])
            await self.send_message_by_expressor(error_templates[3] if len(error_templates) > 3 else "禁言时长必须是数字哦~")
            return False, error_msg

        # 获取用户ID
        try:
            platform, user_id = await self.get_user_id_by_person_name(target)
        except Exception as e:
            error_msg = f"查找用户ID时出错: {e}"
            logger.error(f"{self.log_prefix} {error_msg}")
            await self.send_message_by_expressor("查找用户信息时出现问题~")
            return False, error_msg

        if not user_id:
            error_msg = f"未找到用户 {target} 的ID"
            await self.send_message_by_expressor(f"找不到 {target} 这个人呢~")
            logger.error(f"{self.log_prefix} {error_msg}")
            return False, error_msg

        # 发送表达情绪的消息
        enable_formatting = self.config.get("enable_duration_formatting", True)
        time_str = self._format_duration(duration_int) if enable_formatting else f"{duration_int}秒"
        
        # 使用模板化消息
        message = self._get_template_message(target, time_str, reason)
        await self.send_message_by_expressor(message)

        try:
            duration_str = str(duration_int)

            # 发送群聊禁言命令，按照新格式
            await self.send_message(
                type="command",
                data={"name": "GROUP_BAN", "args": {"qq_id": str(user_id), "duration": duration_str}},
                display_message=f"尝试禁言了 {target} {time_str}",
            )

            await self.store_action_info(
                action_build_into_prompt=False,
                action_prompt_display=f"你尝试禁言了 {target} {time_str}，理由：{reason}",
            )

            logger.info(f"{self.log_prefix} 成功发送禁言命令，用户 {target}({user_id})，时长 {duration_int} 秒")
            return True, f"成功禁言 {target}，时长 {time_str}"

        except Exception as e:
            logger.error(f"{self.log_prefix} 执行禁言动作时出错: {e}")
            await self.send_message_by_expressor(f"执行禁言动作时出错: {e}")
            return False, f"执行禁言动作时出错: {e}"

    def _format_duration(self, seconds: int) -> str:
        """将秒数格式化为可读的时间字符串"""
        if seconds < 60:
            return f"{seconds}秒"
        elif seconds < 3600:
            minutes = seconds // 60
            remaining_seconds = seconds % 60
            if remaining_seconds > 0:
                return f"{minutes}分{remaining_seconds}秒"
            else:
                return f"{minutes}分钟"
        elif seconds < 86400:
            hours = seconds // 3600
            remaining_minutes = (seconds % 3600) // 60
            if remaining_minutes > 0:
                return f"{hours}小时{remaining_minutes}分钟"
            else:
                return f"{hours}小时"
        else:
            days = seconds // 86400
            remaining_hours = (seconds % 86400) // 3600
            if remaining_hours > 0:
                return f"{days}天{remaining_hours}小时"
            else:
                return f"{days}天"
