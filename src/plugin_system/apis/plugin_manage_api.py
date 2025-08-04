from typing import Tuple, List


def list_loaded_plugins() -> List[str]:
    """
    列出所有当前加载的插件。

    Returns:
        List[str]: 当前加载的插件名称列表。
    """
    from src.plugin_system.core.plugin_manager import plugin_manager

    return plugin_manager.list_loaded_plugins()


def list_registered_plugins() -> List[str]:
    """
    列出所有已注册的插件。

    Returns:
        List[str]: 已注册的插件名称列表。
    """
    from src.plugin_system.core.plugin_manager import plugin_manager

    return plugin_manager.list_registered_plugins()


def get_plugin_path(plugin_name: str) -> str:
    """
    获取指定插件的路径。

    Args:
        plugin_name (str): 插件名称。

    Returns:
        str: 插件目录的绝对路径。
        
    Raises:
        ValueError: 如果插件不存在。
    """
    from src.plugin_system.core.plugin_manager import plugin_manager

    if plugin_path := plugin_manager.get_plugin_path(plugin_name):
        return plugin_path
    else:
        raise ValueError(f"插件 '{plugin_name}' 不存在。")


async def remove_plugin(plugin_name: str) -> bool:
    """
    卸载指定的插件。

    **此函数是异步的，确保在异步环境中调用。**

    Args:
        plugin_name (str): 要卸载的插件名称。

    Returns:
        bool: 卸载是否成功。
    """
    from src.plugin_system.core.plugin_manager import plugin_manager

    return await plugin_manager.remove_registered_plugin(plugin_name)


async def reload_plugin(plugin_name: str) -> bool:
    """
    重新加载指定的插件。

    **此函数是异步的，确保在异步环境中调用。**

    Args:
        plugin_name (str): 要重新加载的插件名称。

    Returns:
        bool: 重新加载是否成功。
    """
    from src.plugin_system.core.plugin_manager import plugin_manager

    return await plugin_manager.reload_registered_plugin(plugin_name)


def load_plugin(plugin_name: str) -> Tuple[bool, int]:
    """
    加载指定的插件。

    Args:
        plugin_name (str): 要加载的插件名称。

    Returns:
        Tuple[bool, int]: 加载是否成功，成功或失败个数。
    """
    from src.plugin_system.core.plugin_manager import plugin_manager

    return plugin_manager.load_registered_plugin_classes(plugin_name)


def add_plugin_directory(plugin_directory: str) -> bool:
    """
    添加插件目录。

    Args:
        plugin_directory (str): 要添加的插件目录路径。
    Returns:
        bool: 添加是否成功。
    """
    from src.plugin_system.core.plugin_manager import plugin_manager

    return plugin_manager.add_plugin_directory(plugin_directory)


def rescan_plugin_directory() -> Tuple[int, int]:
    """
    重新扫描插件目录，加载新插件。
    Returns:
        Tuple[int, int]: 成功加载的插件数量和失败的插件数量。
    """
    from src.plugin_system.core.plugin_manager import plugin_manager

    return plugin_manager.rescan_plugin_directory()
