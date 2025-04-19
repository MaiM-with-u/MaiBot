from typing import List, Any, Optional, Type
import inspect
import importlib
import pkgutil
import os
from src.common.logger_manager import get_logger
from rich.traceback import install

install(extra_lines=3)

logger = get_logger("base_tool")

# 工具注册表
TOOL_REGISTRY = {}


class BaseTool:
    """所有工具的基类"""

    # 工具名称，子类必须重写
    name = None
    # 工具描述，子类必须重写
    description = None
    # 工具参数定义，子类必须重写
    parameters = None

    @classmethod
    def get_tool_definition(cls) -> dict[str, Any]:
        """获取工具定义，用于LLM工具调用

        Returns:
            dict: 工具定义字典
        """
        if not cls.name or not cls.description or not cls.parameters:
            raise NotImplementedError(f"工具类 {cls.__name__} 必须定义 name, description 和 parameters 属性")

        return {
            "type": "function",
            "function": {"name": cls.name, "description": cls.description, "parameters": cls.parameters},
        }

    async def execute(self, function_args: dict[str, Any]) -> dict[str, Any]:
        """执行工具函数

        Args:
            function_args: 工具调用参数

        Returns:
            dict: 工具执行结果
        """
        raise NotImplementedError("子类必须实现execute方法")


def register_tool(tool_class: Type[BaseTool]):
    """注册工具到全局注册表

    Args:
        tool_class: 工具类
    """
    if not issubclass(tool_class, BaseTool):
        raise TypeError(f"{tool_class.__name__} 不是 BaseTool 的子类")

    tool_name = tool_class.name
    if not tool_name:
        raise ValueError(f"工具类 {tool_class.__name__} 没有定义 name 属性")

    TOOL_REGISTRY[tool_name] = tool_class
    logger.info(f"已注册: {tool_name}")


def discover_tools():
    """自动发现并注册tool_can_use目录下的所有工具"""
    # 获取当前目录路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    package_name = os.path.basename(current_dir)

    # 遍历包中的所有模块
    for _, module_name, _ in pkgutil.iter_modules([current_dir]):
        # 跳过当前模块和__pycache__
        if module_name == "base_tool" or module_name.startswith("__"):
            continue

        # 导入模块
        module = importlib.import_module(f"src.tools.{package_name}.{module_name}")

        # 查找模块中的工具类
        for _, obj in inspect.getmembers(module):
            if inspect.isclass(obj) and issubclass(obj, BaseTool) and obj != BaseTool:
                register_tool(obj)

    logger.info(f"工具发现完成，共注册 {len(TOOL_REGISTRY)} 个工具")


def get_all_tool_definitions() -> List[dict[str, Any]]:
    """获取所有已注册工具的定义

    Returns:
        List[dict]: 工具定义列表
    """
    return [tool_class().get_tool_definition() for tool_class in TOOL_REGISTRY.values()]


def get_tool_instance(tool_name: str) -> Optional[BaseTool]:
    """获取指定名称的工具实例

    Args:
        tool_name: 工具名称

    Returns:
        Optional[BaseTool]: 工具实例，如果找不到则返回None
    """
    tool_class = TOOL_REGISTRY.get(tool_name)
    if not tool_class:
        return None
    return tool_class()


def run_lua_code(lua_code: str):
    """兼容Lua代码运行（小工具）

    Args:
        lua_code (str): Lua代码

    Returns:
        _LuaTable: Lua运行时的全局变量
    """
    try:
        from lupa import LuaRuntime
    except ImportError as e:
        raise ImportError("无法导入lupa模块，请确保已安装lupa库（可通过pip安装）") from e

    lua = LuaRuntime(unpack_returned_tuples=True)
    try:
        lua.execute(lua_code)
        return lua.globals()
    except Exception as e:
        # 返回包含错误信息的字典而不是直接抛出异常，保持函数接口的稳定性
        return {
            "__error__": True,
            "type": type(e).__name__,
            "message": "Lua 代码执行出错，请检查代码是否正确。",
            "lua_code": lua_code,  # 可选：返回出错的代码片段便于调试
        }
