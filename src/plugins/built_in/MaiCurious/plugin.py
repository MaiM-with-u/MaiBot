from typing import List, Tuple, Type, Any

# 导入新插件系统
from src.plugin_system import BasePlugin, register_plugin, ComponentInfo
from src.plugin_system.base.config_types import ConfigField
from src.person_info.person_info import Person
from src.plugin_system.base.base_tool import BaseTool, ToolParamType

# 导入依赖的系统组件
from src.common.logger import get_logger

from src.plugins.built_in.relation.relation import BuildRelationAction
from src.plugin_system.apis import llm_api
from src.plugin_system.base.base_action import BaseAction
from src.plugin_system.base.component_types import ActionActivationType
from src.plugin_system.apis import config_api
from src.plugin_system.apis import frequency_api
from src.plugin_system.apis import generator_api
from src.memory_system.questions import global_conflict_tracker

logger = get_logger("question_actions")



class CuriousAction(BaseAction):
    """频率调节动作 - 调整聊天发言频率"""

    activation_type = ActionActivationType.ALWAYS
    parallel_action = True

    # 动作基本信息
    action_name = "make_question"
    
    action_description = "提出一个问题，当有人反驳你的观点，或其他人之间有观点冲突时使用"

    # 动作参数定义
    action_parameters = {
        "question": "对存在疑问的信息提出一个问题，描述全面，完整的描述涉及的概念和问题",
    }
    
    action_require = [
        f"当聊天记录中的概念存在逻辑上的矛盾时使用",
        f"当有人反对或否定你提出的信息时使用",
        f"或当你对现有的概念或事物存在疑问时使用",
        f"有人认为你的观点是错误的，请选择question动作",
        f"有人与你观点不一致，请选择question动作",
    ]

    # 关联类型
    associated_types = ["text"]

    async def execute(self) -> Tuple[bool, str]:
        """执行频率调节动作"""
        try:
            if len(global_conflict_tracker.question_tracker_list) > 1:
                return False, "当前已有问题，请先解答完再提问，不要再使用make_question动作"
            
            question = self.action_data.get("question", "")

            # 存储问题到冲突追踪器
            if question:
                await global_conflict_tracker.record_conflict(conflict_content=question, start_following=True,chat_id=self.chat_id)
                logger.info(f"已存储问题到冲突追踪器: {question}")
                await self.store_action_info(
                    action_build_into_prompt=True,
                    action_prompt_display=f"你产生了一个问题：{question}",
                    action_done=True,
                )
            return True, f"问题{question}已记录，不要重复提问该问题"
        except Exception as e:
            error_msg = f"问题生成失败: {str(e)}"
            logger.error(f"{self.log_prefix} {error_msg}", exc_info=True)
            await self.send_text("问题生成失败")
            return False, error_msg


@register_plugin
class CuriousPlugin(BasePlugin):
    """关系动作插件

    系统内置插件，提供基础的聊天交互功能：
    - Reply: 回复动作
    - NoReply: 不回复动作
    - Emoji: 表情动作

    注意：插件基本信息优先从_manifest.json文件中读取
    """

    # 插件基本信息
    plugin_name: str = "maicurious"  # 内部标识符
    enable_plugin: bool = True
    dependencies: list[str] = []  # 插件依赖列表
    python_dependencies: list[str] = []  # Python包依赖列表
    config_file_name: str = "config.toml"

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件启用配置",
        "components": "核心组件启用配置",
    }

    # 配置Schema定义
    config_schema: dict = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="3.0.0", description="配置文件版本"),
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""

        # --- 根据配置注册组件 ---
        components = []
        components.append((CuriousAction.get_action_info(), CuriousAction))

        return components
