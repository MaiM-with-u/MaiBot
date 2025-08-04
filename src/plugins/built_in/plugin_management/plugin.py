import asyncio

from typing import List, Tuple, Type
from src.plugin_system import (
    BasePlugin,
    BaseCommand,
    CommandInfo,
    ConfigField,
    register_plugin,
    plugin_manage_api,
    component_manage_api,
    ComponentInfo,
    ComponentType,
    send_api,
)


class ManagementCommand(BaseCommand):
    command_name: str = "management"
    description: str = "管理命令"
    command_pattern: str = r"(?P<manage_command>^/pm(\s[a-zA-Z0-9_]+)*\s*$)"

    async def execute(self) -> Tuple[bool, str, bool]:
        # sourcery skip: merge-duplicate-blocks
        if (
            not self.message
            or not self.message.message_info
            or not self.message.message_info.user_info
            or str(self.message.message_info.user_info.user_id) not in self.get_config("plugin.permission", [])  # type: ignore
        ):
            await self._send_message("你没有权限使用插件管理命令")
            return False, "没有权限", True
        if not self.message.chat_stream:
            await self._send_message("无法获取聊天流信息")
            return False, "无法获取聊天流信息", True
        self.stream_id = self.message.chat_stream.stream_id
        if not self.stream_id:
            await self._send_message("无法获取聊天流信息")
            return False, "无法获取聊天流信息", True
        command_list = self.matched_groups["manage_command"].strip().split(" ")
        if len(command_list) == 1:
            await self.show_help("all")
            return True, "帮助已发送", True
        if len(command_list) == 2:
            match command_list[1]:
                case "plugin":
                    await self.show_help("plugin")
                case "component":
                    await self.show_help("component")
                case "help":
                    await self.show_help("all")
                case _:
                    await self._send_message("插件管理命令不合法")
                    return False, "命令不合法", True
        if len(command_list) == 3:
            if command_list[1] == "plugin":
                match command_list[2]:
                    case "help":
                        await self.show_help("plugin")
                    case "list":
                        await self._list_registered_plugins()
                    case "list_enabled":
                        await self._list_loaded_plugins()
                    case "rescan":
                        await self._rescan_plugin_dirs()
                    case _:
                        await self._send_message("插件管理命令不合法")
                        return False, "命令不合法", True
            elif command_list[1] == "component":
                if command_list[2] == "list":
                    await self._list_all_registered_components()
                elif command_list[2] == "help":
                    await self.show_help("component")
                else:
                    await self._send_message("插件管理命令不合法")
                    return False, "命令不合法", True
            else:
                await self._send_message("插件管理命令不合法")
                return False, "命令不合法", True
        if len(command_list) == 4:
            if command_list[1] == "plugin":
                match command_list[2]:
                    case "load":
                        await self._load_plugin(command_list[3])
                    case "unload":
                        await self._unload_plugin(command_list[3])
                    case "reload":
                        await self._reload_plugin(command_list[3])
                    case "add_dir":
                        await self._add_dir(command_list[3])
                    case _:
                        await self._send_message("插件管理命令不合法")
                        return False, "命令不合法", True
            elif command_list[1] == "component":
                if command_list[2] != "list":
                    await self._send_message("插件管理命令不合法")
                    return False, "命令不合法", True
                if command_list[3] == "enabled":
                    await self._list_enabled_components()
                elif command_list[3] == "disabled":
                    await self._list_disabled_components()
                else:
                    await self._send_message("插件管理命令不合法")
                    return False, "命令不合法", True
            else:
                await self._send_message("插件管理命令不合法")
                return False, "命令不合法", True
        if len(command_list) == 5:
            if command_list[1] != "component":
                await self._send_message("插件管理命令不合法")
                return False, "命令不合法", True
            if command_list[2] != "list":
                await self._send_message("插件管理命令不合法")
                return False, "命令不合法", True
            if command_list[3] == "enabled":
                await self._list_enabled_components(target_type=command_list[4])
            elif command_list[3] == "disabled":
                await self._list_disabled_components(target_type=command_list[4])
            elif command_list[3] == "type":
                await self._list_registered_components_by_type(command_list[4])
            else:
                await self._send_message("插件管理命令不合法")
                return False, "命令不合法", True
        if len(command_list) == 6:
            if command_list[1] != "component":
                await self._send_message("插件管理命令不合法")
                return False, "命令不合法", True
            if command_list[2] == "enable":
                if command_list[3] == "global":
                    await self._globally_enable_component(command_list[4], command_list[5])
                elif command_list[3] == "local":
                    await self._locally_enable_component(command_list[4], command_list[5])
                else:
                    await self._send_message("插件管理命令不合法")
                    return False, "命令不合法", True
            elif command_list[2] == "disable":
                if command_list[3] == "global":
                    await self._globally_disable_component(command_list[4], command_list[5])
                elif command_list[3] == "local":
                    await self._locally_disable_component(command_list[4], command_list[5])
                else:
                    await self._send_message("插件管理命令不合法")
                    return False, "命令不合法", True
            else:
                await self._send_message("插件管理命令不合法")
                return False, "命令不合法", True

        return True, "命令执行完成", True

    async def show_help(self, target: str):
        help_msg = ""
        match target:
            case "all":
                help_msg = (
                    "管理命令帮助\n"
                    "/pm help 管理命令提示\n"
                    "/pm plugin 插件管理命令\n"
                    "/pm component 组件管理命令\n"
                    "使用 /pm plugin help 或 /pm component help 获取具体帮助"
                )
            case "plugin":
                help_msg = (
                    "插件管理命令帮助\n"
                    "/pm plugin help 插件管理命令提示\n"
                    "/pm plugin list 列出所有注册的插件\n"
                    "/pm plugin list_enabled 列出所有加载（启用）的插件\n"
                    "/pm plugin rescan 重新扫描所有目录\n"
                    "/pm plugin load <plugin_name> 加载指定插件\n"
                    "/pm plugin unload <plugin_name> 卸载指定插件\n"
                    "/pm plugin reload <plugin_name> 重新加载指定插件\n"
                    "/pm plugin add_dir <directory_path> 添加插件目录\n"
                )
            case "component":
                help_msg = (
                    "组件管理命令帮助\n"
                    "/pm component help 组件管理命令提示\n"
                    "/pm component list 列出所有注册的组件\n"
                    "/pm component list enabled <可选: type> 列出所有启用的组件\n"
                    "/pm component list disabled <可选: type> 列出所有禁用的组件\n"
                    "  - <type> 可选项: local，代表当前聊天中的；global，代表全局的\n"
                    "  - <type> 不填时为 global\n"
                    "/pm component list type <component_type> 列出已经注册的指定类型的组件\n"
                    "/pm component enable global <component_name> <component_type> 全局启用组件\n"
                    "/pm component enable local <component_name> <component_type> 本聊天启用组件\n"
                    "/pm component disable global <component_name> <component_type> 全局禁用组件\n"
                    "/pm component disable local <component_name> <component_type> 本聊天禁用组件\n"
                    "  - <component_type> 可选项: action, command, event_handler\n"
                )
            case _:
                return
        await self._send_message(help_msg)

    async def _list_loaded_plugins(self):
        plugins = plugin_manage_api.list_loaded_plugins()
        await self._send_message(f"已加载的插件: {', '.join(plugins)}")

    async def _list_registered_plugins(self):
        plugins = plugin_manage_api.list_registered_plugins()
        await self._send_message(f"已注册的插件: {', '.join(plugins)}")

    async def _rescan_plugin_dirs(self):
        plugin_manage_api.rescan_plugin_directory()
        await self._send_message("插件目录重新扫描执行中")

    async def _load_plugin(self, plugin_name: str):
        success, count = plugin_manage_api.load_plugin(plugin_name)
        if success:
            await self._send_message(f"插件加载成功: {plugin_name}")
        else:
            if count == 0:
                await self._send_message(f"插件{plugin_name}为禁用状态")
            await self._send_message(f"插件加载失败: {plugin_name}")

    async def _unload_plugin(self, plugin_name: str):
        success = await plugin_manage_api.remove_plugin(plugin_name)
        if success:
            await self._send_message(f"插件卸载成功: {plugin_name}")
        else:
            await self._send_message(f"插件卸载失败: {plugin_name}")

    async def _reload_plugin(self, plugin_name: str):
        success = await plugin_manage_api.reload_plugin(plugin_name)
        if success:
            await self._send_message(f"插件重新加载成功: {plugin_name}")
        else:
            await self._send_message(f"插件重新加载失败: {plugin_name}")

    async def _add_dir(self, dir_path: str):
        await self._send_message(f"正在添加插件目录: {dir_path}")
        success = plugin_manage_api.add_plugin_directory(dir_path)
        await asyncio.sleep(0.5)  # 防止乱序发送
        if success:
            await self._send_message(f"插件目录添加成功: {dir_path}")
        else:
            await self._send_message(f"插件目录添加失败: {dir_path}")

    def _fetch_all_registered_components(self) -> List[ComponentInfo]:
        all_plugin_info = component_manage_api.get_all_plugin_info()
        if not all_plugin_info:
            return []

        components_info: List[ComponentInfo] = []
        for plugin_info in all_plugin_info.values():
            components_info.extend(plugin_info.components)
        return components_info

    def _fetch_locally_disabled_components(self) -> List[str]:
        locally_disabled_components_actions = component_manage_api.get_locally_disabled_components(
            self.message.chat_stream.stream_id, ComponentType.ACTION
        )
        locally_disabled_components_commands = component_manage_api.get_locally_disabled_components(
            self.message.chat_stream.stream_id, ComponentType.COMMAND
        )
        locally_disabled_components_event_handlers = component_manage_api.get_locally_disabled_components(
            self.message.chat_stream.stream_id, ComponentType.EVENT_HANDLER
        )
        return (
            locally_disabled_components_actions
            + locally_disabled_components_commands
            + locally_disabled_components_event_handlers
        )

    async def _list_all_registered_components(self):
        components_info = self._fetch_all_registered_components()
        if not components_info:
            await self._send_message("没有注册的组件")
            return

        all_components_str = ", ".join(
            f"{component.name} ({component.component_type})" for component in components_info
        )
        await self._send_message(f"已注册的组件: {all_components_str}")

    async def _list_enabled_components(self, target_type: str = "global"):
        components_info = self._fetch_all_registered_components()
        if not components_info:
            await self._send_message("没有注册的组件")
            return

        if target_type == "global":
            enabled_components = [component for component in components_info if component.enabled]
            if not enabled_components:
                await self._send_message("没有满足条件的已启用全局组件")
                return
            enabled_components_str = ", ".join(
                f"{component.name} ({component.component_type})" for component in enabled_components
            )
            await self._send_message(f"满足条件的已启用全局组件: {enabled_components_str}")
        elif target_type == "local":
            locally_disabled_components = self._fetch_locally_disabled_components()
            enabled_components = [
                component
                for component in components_info
                if (component.name not in locally_disabled_components and component.enabled)
            ]
            if not enabled_components:
                await self._send_message("本聊天没有满足条件的已启用组件")
                return
            enabled_components_str = ", ".join(
                f"{component.name} ({component.component_type})" for component in enabled_components
            )
            await self._send_message(f"本聊天满足条件的已启用组件: {enabled_components_str}")

    async def _list_disabled_components(self, target_type: str = "global"):
        components_info = self._fetch_all_registered_components()
        if not components_info:
            await self._send_message("没有注册的组件")
            return

        if target_type == "global":
            disabled_components = [component for component in components_info if not component.enabled]
            if not disabled_components:
                await self._send_message("没有满足条件的已禁用全局组件")
                return
            disabled_components_str = ", ".join(
                f"{component.name} ({component.component_type})" for component in disabled_components
            )
            await self._send_message(f"满足条件的已禁用全局组件: {disabled_components_str}")
        elif target_type == "local":
            locally_disabled_components = self._fetch_locally_disabled_components()
            disabled_components = [
                component
                for component in components_info
                if (component.name in locally_disabled_components or not component.enabled)
            ]
            if not disabled_components:
                await self._send_message("本聊天没有满足条件的已禁用组件")
                return
            disabled_components_str = ", ".join(
                f"{component.name} ({component.component_type})" for component in disabled_components
            )
            await self._send_message(f"本聊天满足条件的已禁用组件: {disabled_components_str}")

    async def _list_registered_components_by_type(self, target_type: str):
        match target_type:
            case "action":
                component_type = ComponentType.ACTION
            case "command":
                component_type = ComponentType.COMMAND
            case "event_handler":
                component_type = ComponentType.EVENT_HANDLER
            case _:
                await self._send_message(f"未知组件类型: {target_type}")
                return

        components_info = component_manage_api.get_components_info_by_type(component_type)
        if not components_info:
            await self._send_message(f"没有注册的 {target_type} 组件")
            return

        components_str = ", ".join(
            f"{name} ({component.component_type})" for name, component in components_info.items()
        )
        await self._send_message(f"注册的 {target_type} 组件: {components_str}")

    async def _globally_enable_component(self, component_name: str, component_type: str):
        match component_type:
            case "action":
                target_component_type = ComponentType.ACTION
            case "command":
                target_component_type = ComponentType.COMMAND
            case "event_handler":
                target_component_type = ComponentType.EVENT_HANDLER
            case _:
                await self._send_message(f"未知组件类型: {component_type}")
                return
        if component_manage_api.globally_enable_component(component_name, target_component_type):
            await self._send_message(f"全局启用组件成功: {component_name}")
        else:
            await self._send_message(f"全局启用组件失败: {component_name}")

    async def _globally_disable_component(self, component_name: str, component_type: str):
        match component_type:
            case "action":
                target_component_type = ComponentType.ACTION
            case "command":
                target_component_type = ComponentType.COMMAND
            case "event_handler":
                target_component_type = ComponentType.EVENT_HANDLER
            case _:
                await self._send_message(f"未知组件类型: {component_type}")
                return
        success = await component_manage_api.globally_disable_component(component_name, target_component_type)
        if success:
            await self._send_message(f"全局禁用组件成功: {component_name}")
        else:
            await self._send_message(f"全局禁用组件失败: {component_name}")

    async def _locally_enable_component(self, component_name: str, component_type: str):
        match component_type:
            case "action":
                target_component_type = ComponentType.ACTION
            case "command":
                target_component_type = ComponentType.COMMAND
            case "event_handler":
                target_component_type = ComponentType.EVENT_HANDLER
            case _:
                await self._send_message(f"未知组件类型: {component_type}")
                return
        if component_manage_api.locally_enable_component(
            component_name,
            target_component_type,
            self.message.chat_stream.stream_id,
        ):
            await self._send_message(f"本地启用组件成功: {component_name}")
        else:
            await self._send_message(f"本地启用组件失败: {component_name}")

    async def _locally_disable_component(self, component_name: str, component_type: str):
        match component_type:
            case "action":
                target_component_type = ComponentType.ACTION
            case "command":
                target_component_type = ComponentType.COMMAND
            case "event_handler":
                target_component_type = ComponentType.EVENT_HANDLER
            case _:
                await self._send_message(f"未知组件类型: {component_type}")
                return
        if component_manage_api.locally_disable_component(
            component_name,
            target_component_type,
            self.message.chat_stream.stream_id,
        ):
            await self._send_message(f"本地禁用组件成功: {component_name}")
        else:
            await self._send_message(f"本地禁用组件失败: {component_name}")
    
    async def _send_message(self, message: str):
        await send_api.text_to_stream(message, self.stream_id, typing=False, storage_message=False)


@register_plugin
class PluginManagementPlugin(BasePlugin):
    plugin_name: str = "plugin_management_plugin"
    enable_plugin: bool = False
    dependencies: list[str] = []
    python_dependencies: list[str] = []
    config_file_name: str = "config.toml"
    config_schema: dict = {
        "plugin": {
            "enabled": ConfigField(bool, default=False, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="1.1.0", description="配置文件版本"),
            "permission": ConfigField(
                list, default=[], description="有权限使用插件管理命令的用户列表，请填写字符串形式的用户ID"
            ),
        },
    }

    def get_plugin_components(self) -> List[Tuple[CommandInfo, Type[BaseCommand]]]:
        components = []
        if self.get_config("plugin.enabled", True):
            components.append((ManagementCommand.get_command_info(), ManagementCommand))
        return components
