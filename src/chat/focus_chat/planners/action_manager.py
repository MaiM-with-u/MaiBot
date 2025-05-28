from typing import Dict, List, Optional, Type, Any
from src.chat.focus_chat.planners.actions.base_action import BaseAction, _ACTION_REGISTRY
from src.chat.heart_flow.observation.observation import Observation
from src.chat.focus_chat.expressors.default_expressor import DefaultExpressor
from src.chat.message_receive.chat_stream import ChatStream
from src.common.logger_manager import get_logger
import importlib
import pkgutil
import os

# 导入动作类，确保装饰器被执行
import src.chat.focus_chat.planners.actions  # noqa

logger = get_logger("action_manager")

# 定义动作信息类型
ActionInfo = Dict[str, Any]


class ActionManager:
    """
    动作管理器，用于管理各种类型的动作
    """

    def __init__(self):
        """初始化动作管理器"""
        # 所有注册的动作集合
        self._registered_actions: Dict[str, ActionInfo] = {}
        # 当前正在使用的动作集合，默认加载默认动作
        self._using_actions: Dict[str, ActionInfo] = {}

        # 默认动作集，仅作为快照，用于恢复默认
        self._default_actions: Dict[str, ActionInfo] = {}

        # 加载所有已注册动作
        self._load_registered_actions()

        # 加载插件动作
        self._load_plugin_actions()

        # 初始化时将默认动作加载到使用中的动作
        self._using_actions = self._default_actions.copy()

    def _load_registered_actions(self) -> None:
        """
        加载所有通过装饰器注册的动作
        """
        try:
            # 从_ACTION_REGISTRY获取所有已注册动作
            for action_name, action_class in _ACTION_REGISTRY.items():
                # 获取动作相关信息

                # 不读取插件动作和基类
                if action_name == "base_action" or action_name == "plugin_action":
                    continue

                action_description: str = getattr(action_class, "action_description", "")
                action_parameters: dict[str:str] = getattr(action_class, "action_parameters", {})
                action_require: list[str] = getattr(action_class, "action_require", [])
                associated_types: list[str] = getattr(action_class, "associated_types", [])
                is_default: bool = getattr(action_class, "default", False)

                if action_name and action_description:
                    # 创建动作信息字典
                    action_info = {
                        "description": action_description,
                        "parameters": action_parameters,
                        "require": action_require,
                        "associated_types": associated_types,
                    }

                    # 添加到所有已注册的动作
                    self._registered_actions[action_name] = action_info

                    # 添加到默认动作（如果是默认动作）
                    if is_default:
                        self._default_actions[action_name] = action_info

            # logger.info(f"所有注册动作: {list(self._registered_actions.keys())}")
            # logger.info(f"默认动作: {list(self._default_actions.keys())}")
            # for action_name, action_info in self._default_actions.items():
            # logger.info(f"动作名称: {action_name}, 动作信息: {action_info}")

        except Exception as e:
            logger.error(f"加载已注册动作失败: {e}")

    def _load_plugin_actions(self) -> None:
        """
        加载所有插件目录中的动作
        """
        try:
            # 检查插件目录是否存在
            plugin_path = "src.plugins"
            plugin_dir = plugin_path.replace(".", os.path.sep)
            if not os.path.exists(plugin_dir):
                logger.info(f"插件目录 {plugin_dir} 不存在，跳过插件动作加载")
                return

            # 导入插件包
            try:
                plugins_package = importlib.import_module(plugin_path)
            except ImportError as e:
                logger.error(f"导入插件包失败: {e}")
                return

            # 遍历插件包中的所有子包
            for _, plugin_name, is_pkg in pkgutil.iter_modules(
                plugins_package.__path__, plugins_package.__name__ + "."
            ):
                if not is_pkg:
                    continue

                # 检查插件是否有actions子包
                plugin_actions_path = f"{plugin_name}.actions"
                try:
                    # 尝试导入插件的actions包
                    importlib.import_module(plugin_actions_path)
                    logger.info(f"成功加载插件动作模块: {plugin_actions_path}")
                except ImportError as e:
                    logger.debug(f"插件 {plugin_name} 没有actions子包或导入失败: {e}")
                    continue

            # 再次从_ACTION_REGISTRY获取所有动作（包括刚刚从插件加载的）
            self._load_registered_actions()

        except Exception as e:
            logger.error(f"加载插件动作失败: {e}")

    def create_action(
        self,
        action_name: str,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        observations: List[Observation],
        expressor: DefaultExpressor,
        chat_stream: ChatStream,
        log_prefix: str,
        shutting_down: bool = False,
    ) -> Optional[BaseAction]:
        """
        创建动作处理器实例

        Args:
            action_name: 动作名称
            action_data: 动作数据
            reasoning: 执行理由
            cycle_timers: 计时器字典
            thinking_id: 思考ID
            observations: 观察列表
            expressor: 表达器
            chat_stream: 聊天流
            log_prefix: 日志前缀
            shutting_down: 是否正在关闭

        Returns:
            Optional[BaseAction]: 创建的动作处理器实例，如果动作名称未注册则返回None
        """
        # 检查动作是否在当前使用的动作集中
        # if action_name not in self._using_actions:
        # logger.warning(f"当前不可用的动作类型: {action_name}")
        # return None

        handler_class = _ACTION_REGISTRY.get(action_name)
        if not handler_class:
            logger.warning(f"未注册的动作类型: {action_name}")
            return None

        try:
            # 创建动作实例
            instance = handler_class(
                action_data=action_data,
                reasoning=reasoning,
                cycle_timers=cycle_timers,
                thinking_id=thinking_id,
                observations=observations,
                expressor=expressor,
                chat_stream=chat_stream,
                log_prefix=log_prefix,
                shutting_down=shutting_down,
            )

            return instance

        except Exception as e:
            logger.error(f"创建动作处理器实例失败: {e}")
            return None

    def get_registered_actions(self) -> Dict[str, ActionInfo]:
        """获取所有已注册的动作集"""
        return self._registered_actions.copy()

    def get_default_actions(self) -> Dict[str, ActionInfo]:
        """获取默认动作集"""
        return self._default_actions.copy()

    def get_using_actions(self) -> Dict[str, ActionInfo]:
        """获取当前正在使用的动作集"""
        return self._using_actions.copy()

    def add_action_to_using(self, action_name: str) -> bool:
        """
        添加已注册的动作到当前使用的动作集

        Args:
            action_name: 动作名称

        Returns:
            bool: 添加是否成功
        """
        if action_name not in self._registered_actions:
            logger.warning(f"添加失败: 动作 {action_name} 未注册")
            return False

        if action_name in self._using_actions:
            logger.info(f"动作 {action_name} 已经在使用中")
            return True

        self._using_actions[action_name] = self._registered_actions[action_name]
        logger.info(f"添加动作 {action_name} 到使用集")
        return True

    def remove_action_from_using(self, action_name: str) -> bool:
        """
        从当前使用的动作集中移除指定动作

        Args:
            action_name: 动作名称

        Returns:
            bool: 移除是否成功
        """
        if action_name not in self._using_actions:
            logger.warning(f"移除失败: 动作 {action_name} 不在当前使用的动作集中")
            return False

        del self._using_actions[action_name]
        logger.info(f"已从使用集中移除动作 {action_name}")
        return True

    def add_action(self, action_name: str, description: str, parameters: Dict = None, require: List = None) -> bool:
        """
        添加新的动作到注册集

        Args:
            action_name: 动作名称
            description: 动作描述
            parameters: 动作参数定义，默认为空字典
            require: 动作依赖项，默认为空列表

        Returns:
            bool: 添加是否成功
        """
        if action_name in self._registered_actions:
            return False

        if parameters is None:
            parameters = {}
        if require is None:
            require = []

        action_info = {"description": description, "parameters": parameters, "require": require}

        self._registered_actions[action_name] = action_info
        return True

    def remove_action(self, action_name: str) -> bool:
        """从注册集移除指定动作"""
        if action_name not in self._registered_actions:
            return False
        del self._registered_actions[action_name]
        # 如果在使用集中也存在，一并移除
        if action_name in self._using_actions:
            del self._using_actions[action_name]
        return True

    def temporarily_remove_actions(self, actions_to_remove: List[str]) -> None:
        """临时移除使用集中的指定动作"""
        for name in actions_to_remove:
            self._using_actions.pop(name, None)

    def restore_actions(self) -> None:
        """恢复到默认动作集"""
        logger.debug(
            f"恢复动作集: 从 {list(self._using_actions.keys())} 恢复到默认动作集 {list(self._default_actions.keys())}"
        )
        self._using_actions = self._default_actions.copy()

    def restore_default_actions(self) -> None:
        """恢复默认动作集到使用集"""
        self._using_actions = self._default_actions.copy()

    def get_action(self, action_name: str) -> Optional[Type[BaseAction]]:
        """
        获取指定动作的处理器类

        Args:
            action_name: 动作名称

        Returns:
            Optional[Type[BaseAction]]: 动作处理器类，如果不存在则返回None
        """
        return _ACTION_REGISTRY.get(action_name)
