from typing import Dict, List, Optional, Callable, Tuple, Type, Any
import os
from importlib.util import spec_from_file_location, module_from_spec
from inspect import getmodule
from pathlib import Path
import traceback

from src.common.logger import get_logger
from src.plugin_system.events.events import EventType
from src.plugin_system.core.component_registry import component_registry
from src.plugin_system.core.dependency_manager import dependency_manager
from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.component_types import ComponentType, PluginInfo, PythonDependency
from src.plugin_system.utils.manifest_utils import VersionComparator

logger = get_logger("plugin_manager")


class PluginManager:
    """
    插件管理器类

    负责加载，重载和卸载插件，同时管理插件的所有组件
    """

    def __init__(self):
        self.plugin_directories: List[str] = []
        self.loaded_plugins: Dict[str, "BasePlugin"] = {}
        self.failed_plugins: Dict[str, str] = {}
        self.plugin_paths: Dict[str, str] = {}  # 记录插件名到目录路径的映射
        self.events_subscriptions: Dict[EventType, List[Callable]] = {}
        self.plugin_classes: Dict[str, Type[BasePlugin]] = {}  # 全局插件类注册表

        # 确保插件目录存在
        self._ensure_plugin_directories()
        logger.info("插件管理器初始化完成")

    def _ensure_plugin_directories(self) -> None:
        """确保所有插件根目录存在，如果不存在则创建"""
        default_directories = ["src/plugins/built_in", "plugins"]

        for directory in default_directories:
            if not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
                logger.info(f"创建插件根目录: {directory}")
            if directory not in self.plugin_directories:
                self.plugin_directories.append(directory)
                logger.debug(f"已添加插件根目录: {directory}")
            else:
                logger.warning(f"根目录不可重复加载: {directory}")

    def add_plugin_directory(self, directory: str) -> bool:
        """添加插件目录"""
        if os.path.exists(directory):
            if directory not in self.plugin_directories:
                self.plugin_directories.append(directory)
                logger.debug(f"已添加插件目录: {directory}")
                return True
            else:
                logger.warning(f"插件不可重复加载: {directory}")
        else:
            logger.warning(f"插件目录不存在: {directory}")
        return False

    def load_all_plugins(self) -> Tuple[int, int]:
        """加载所有插件

        Returns:
            tuple[int, int]: (插件数量, 组件数量)
        """
        logger.debug("开始加载所有插件...")

        # 第一阶段：加载所有插件模块（注册插件类）
        total_loaded_modules = 0
        total_failed_modules = 0

        for directory in self.plugin_directories:
            loaded, failed = self._load_plugin_modules_from_directory(directory)
            total_loaded_modules += loaded
            total_failed_modules += failed

        logger.debug(f"插件模块加载完成 - 成功: {total_loaded_modules}, 失败: {total_failed_modules}")

        total_registered = 0
        total_failed_registration = 0

        for plugin_name in self.plugin_classes.keys():
            if self.load_registered_plugin_classes(plugin_name):
                total_registered += 1
            else:
                total_failed_registration += 1

    def load_registered_plugin_classes(self, plugin_name: str) -> bool:
        # sourcery skip: extract-duplicate-method, extract-method
        """
        加载已经注册的插件类
        """
        plugin_class: Type[BasePlugin] = self.plugin_classes.get(plugin_name)
        if not plugin_class:
            logger.error(f"插件 {plugin_name} 的插件类未注册或不存在")
            return False
        try:
            # 使用记录的插件目录路径
            plugin_dir = self.plugin_paths.get(plugin_name)

            # 如果没有记录，则尝试查找（fallback）
            if not plugin_dir:
                plugin_dir = self._find_plugin_directory(plugin_class)
                if plugin_dir:
                    self.plugin_paths[plugin_name] = plugin_dir  # 更新路径
            plugin_instance = plugin_class(plugin_dir=plugin_dir)  # 实例化插件（可能因为缺少manifest而失败）
            # 检查插件是否启用
            if not plugin_instance.enable_plugin:
                logger.info(f"插件 {plugin_name} 已禁用，跳过加载")
                return False

            # 检查版本兼容性
            is_compatible, compatibility_error = self._check_plugin_version_compatibility(
                plugin_name, plugin_instance.manifest_data
            )
            if not is_compatible:
                self.failed_plugins[plugin_name] = compatibility_error
                logger.error(f"❌ 插件加载失败: {plugin_name} - {compatibility_error}")
                return False
            if plugin_instance.register_plugin():
                self.loaded_plugins[plugin_name] = plugin_instance
                self._show_plugin_components(plugin_name)
                return True
            else:
                self.failed_plugins[plugin_name] = "插件注册失败"
                logger.error(f"❌ 插件注册失败: {plugin_name}")
                return False

        except FileNotFoundError as e:
            # manifest文件缺失
            error_msg = f"缺少manifest文件: {str(e)}"
            self.failed_plugins[plugin_name] = error_msg
            logger.error(f"❌ 插件加载失败: {plugin_name} - {error_msg}")
            return False

        except ValueError as e:
            # manifest文件格式错误或验证失败
            traceback.print_exc()
            error_msg = f"manifest验证失败: {str(e)}"
            self.failed_plugins[plugin_name] = error_msg
            logger.error(f"❌ 插件加载失败: {plugin_name} - {error_msg}")
            return False

        except Exception as e:
            # 其他错误
            error_msg = f"未知错误: {str(e)}"
            self.failed_plugins[plugin_name] = error_msg
            logger.error(f"❌ 插件加载失败: {plugin_name} - {error_msg}")
            logger.debug("详细错误信息: ", exc_info=True)
            return False

    def unload_registered_plugin_module(self, plugin_name: str) -> None:
        """
        卸载插件模块
        """
        pass

    def reload_registered_plugin_module(self, plugin_name: str) -> None:
        """
        重载插件模块
        """
        self.unload_registered_plugin_module(plugin_name)
        self.load_registered_plugin_classes(plugin_name)

    def rescan_plugin_directory(self) -> None:
        """
        重新扫描插件根目录
        """
        for directory in self.plugin_directories:
            if os.path.exists(directory):
                logger.debug(f"重新扫描插件根目录: {directory}")
                self._load_plugin_modules_from_directory(directory)
            else:
                logger.warning(f"插件根目录不存在: {directory}")
    
    def get_loaded_plugins(self) -> List[PluginInfo]:
        """获取所有已加载的插件信息"""
        return list(component_registry.get_all_plugins().values())

    def get_enabled_plugins(self) -> List[PluginInfo]:
        """获取所有启用的插件信息"""
        return list(component_registry.get_enabled_plugins().values())
    
    def enable_plugin(self, plugin_name: str) -> bool:
        # -------------------------------- NEED REFACTORING --------------------------------
        """启用插件"""
        if plugin_info := component_registry.get_plugin_info(plugin_name):
            plugin_info.enabled = True
            # 启用插件的所有组件
            for component in plugin_info.components:
                component_registry.enable_component(component.name)
            logger.debug(f"已启用插件: {plugin_name}")
            return True
        return False

    def disable_plugin(self, plugin_name: str) -> bool:
        # -------------------------------- NEED REFACTORING --------------------------------
        """禁用插件"""
        if plugin_info := component_registry.get_plugin_info(plugin_name):
            plugin_info.enabled = False
            # 禁用插件的所有组件
            for component in plugin_info.components:
                component_registry.disable_component(component.name)
            logger.debug(f"已禁用插件: {plugin_name}")
            return True
        return False

    def get_plugin_instance(self, plugin_name: str) -> Optional["BasePlugin"]:
        """获取插件实例

        Args:
            plugin_name: 插件名称

        Returns:
            Optional[BasePlugin]: 插件实例或None
        """
        return self.loaded_plugins.get(plugin_name)
    
    def get_plugin_stats(self) -> Dict[str, Any]:
        """获取插件统计信息"""
        all_plugins = component_registry.get_all_plugins()
        enabled_plugins = component_registry.get_enabled_plugins()

        action_components = component_registry.get_components_by_type(ComponentType.ACTION)
        command_components = component_registry.get_components_by_type(ComponentType.COMMAND)

        return {
            "total_plugins": len(all_plugins),
            "enabled_plugins": len(enabled_plugins),
            "failed_plugins": len(self.failed_plugins),
            "total_components": len(action_components) + len(command_components),
            "action_components": len(action_components),
            "command_components": len(command_components),
            "loaded_plugin_files": len(self.loaded_plugins),
            "failed_plugin_details": self.failed_plugins.copy(),
        }
    
    def check_all_dependencies(self, auto_install: bool = False) -> Dict[str, any]:
        """检查所有插件的Python依赖包

        Args:
            auto_install: 是否自动安装缺失的依赖包

        Returns:
            Dict[str, any]: 检查结果摘要
        """
        logger.info("开始检查所有插件的Python依赖包...")

        all_required_missing: List[PythonDependency] = []
        all_optional_missing: List[PythonDependency] = []
        plugin_status = {}

        for plugin_name, _plugin_instance in self.loaded_plugins.items():
            plugin_info = component_registry.get_plugin_info(plugin_name)
            if not plugin_info or not plugin_info.python_dependencies:
                plugin_status[plugin_name] = {"status": "no_dependencies", "missing": []}
                continue

            logger.info(f"检查插件 {plugin_name} 的依赖...")

            missing_required, missing_optional = dependency_manager.check_dependencies(plugin_info.python_dependencies)

            if missing_required:
                all_required_missing.extend(missing_required)
                plugin_status[plugin_name] = {
                    "status": "missing_required",
                    "missing": [dep.package_name for dep in missing_required],
                    "optional_missing": [dep.package_name for dep in missing_optional],
                }
                logger.error(f"插件 {plugin_name} 缺少必需依赖: {[dep.package_name for dep in missing_required]}")
            elif missing_optional:
                all_optional_missing.extend(missing_optional)
                plugin_status[plugin_name] = {
                    "status": "missing_optional",
                    "missing": [],
                    "optional_missing": [dep.package_name for dep in missing_optional],
                }
                logger.warning(f"插件 {plugin_name} 缺少可选依赖: {[dep.package_name for dep in missing_optional]}")
            else:
                plugin_status[plugin_name] = {"status": "ok", "missing": []}
                logger.info(f"插件 {plugin_name} 依赖检查通过")

        # 汇总结果
        total_missing = len({dep.package_name for dep in all_required_missing})
        total_optional_missing = len({dep.package_name for dep in all_optional_missing})

        logger.info(f"依赖检查完成 - 缺少必需包: {total_missing}个, 缺少可选包: {total_optional_missing}个")

        # 如果需要自动安装
        install_success = True
        if auto_install and all_required_missing:
            unique_required = {dep.package_name: dep for dep in all_required_missing}
            logger.info(f"开始自动安装 {len(unique_required)} 个必需依赖包...")
            install_success = dependency_manager.install_dependencies(list(unique_required.values()), auto_install=True)

        return {
            "total_plugins_checked": len(plugin_status),
            "plugins_with_missing_required": len(
                [p for p in plugin_status.values() if p["status"] == "missing_required"]
            ),
            "plugins_with_missing_optional": len(
                [p for p in plugin_status.values() if p["status"] == "missing_optional"]
            ),
            "total_missing_required": total_missing,
            "total_missing_optional": total_optional_missing,
            "plugin_status": plugin_status,
            "auto_install_attempted": auto_install and bool(all_required_missing),
            "auto_install_success": install_success,
            "install_summary": dependency_manager.get_install_summary(),
        }

    def generate_plugin_requirements(self, output_path: str = "plugin_requirements.txt") -> bool:
        """生成所有插件依赖的requirements文件

        Args:
            output_path: 输出文件路径

        Returns:
            bool: 生成是否成功
        """
        logger.info("开始生成插件依赖requirements文件...")

        all_dependencies = []

        for plugin_name, _plugin_instance in self.loaded_plugins.items():
            plugin_info = component_registry.get_plugin_info(plugin_name)
            if plugin_info and plugin_info.python_dependencies:
                all_dependencies.append(plugin_info.python_dependencies)

        if not all_dependencies:
            logger.info("没有找到任何插件依赖")
            return False

        return dependency_manager.generate_requirements_file(all_dependencies, output_path)

    def _load_plugin_modules_from_directory(self, directory: str) -> tuple[int, int]:
        """从指定目录加载插件模块"""
        loaded_count = 0
        failed_count = 0

        if not os.path.exists(directory):
            logger.warning(f"插件根目录不存在: {directory}")
            return 0, 1

        logger.debug(f"正在扫描插件根目录: {directory}")

        # 遍历目录中的所有Python文件和包
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)

            if os.path.isfile(item_path) and item.endswith(".py") and item != "__init__.py":
                # 单文件插件
                plugin_name = Path(item_path).stem
                if self._load_plugin_module_file(item_path, plugin_name, directory):
                    loaded_count += 1
                else:
                    failed_count += 1

            elif os.path.isdir(item_path) and not item.startswith(".") and not item.startswith("__"):
                # 插件包
                plugin_file = os.path.join(item_path, "plugin.py")
                if os.path.exists(plugin_file):
                    plugin_name = item  # 使用目录名作为插件名
                    if self._load_plugin_module_file(plugin_file, plugin_name, item_path):
                        loaded_count += 1
                    else:
                        failed_count += 1

        return loaded_count, failed_count

    def _find_plugin_directory(self, plugin_class: str) -> Optional[str]:
        """查找插件类对应的目录路径"""
        try:
            module = getmodule(plugin_class)
            if module and hasattr(module, "__file__") and module.__file__:
                return os.path.dirname(module.__file__)
        except Exception as e:
            logger.debug(f"通过inspect获取插件目录失败: {e}")
        return None

    def _load_plugin_module_file(self, plugin_file: str, plugin_name: str, plugin_dir: str) -> bool:
        # sourcery skip: extract-method
        """加载单个插件模块文件

        Args:
            plugin_file: 插件文件路径
            plugin_name: 插件名称
            plugin_dir: 插件目录路径
        """
        # 生成模块名
        plugin_path = Path(plugin_file)
        if plugin_path.parent.name != "plugins":
            # 插件包格式：parent_dir.plugin
            module_name = f"plugins.{plugin_path.parent.name}.plugin"
        else:
            # 单文件格式：plugins.filename
            module_name = f"plugins.{plugin_path.stem}"

        try:
            # 动态导入插件模块
            spec = spec_from_file_location(module_name, plugin_file)
            if spec is None or spec.loader is None:
                logger.error(f"无法创建模块规范: {plugin_file}")
                return False

            module = module_from_spec(spec)
            spec.loader.exec_module(module)

            # 记录插件名和目录路径的映射
            self.plugin_paths[plugin_name] = plugin_dir

            logger.debug(f"插件模块加载成功: {plugin_file}")
            return True

        except Exception as e:
            error_msg = f"加载插件模块 {plugin_file} 失败: {e}"
            logger.error(error_msg)
            self.failed_plugins[plugin_name] = error_msg
            return False

    def _check_plugin_version_compatibility(self, plugin_name: str, manifest_data: Dict[str, Any]) -> Tuple[bool, str]:
        """检查插件版本兼容性

        Args:
            plugin_name: 插件名称
            manifest_data: manifest数据

        Returns:
            Tuple[bool, str]: (是否兼容, 错误信息)
        """
        if "host_application" not in manifest_data:
            return True, ""  # 没有版本要求，默认兼容

        host_app = manifest_data["host_application"]
        if not isinstance(host_app, dict):
            return True, ""

        min_version = host_app.get("min_version", "")
        max_version = host_app.get("max_version", "")

        if not min_version and not max_version:
            return True, ""  # 没有版本要求，默认兼容

        try:
            current_version = VersionComparator.get_current_host_version()
            is_compatible, error_msg = VersionComparator.is_version_in_range(current_version, min_version, max_version)
            if not is_compatible:
                return False, f"版本不兼容: {error_msg}"
            logger.debug(f"插件 {plugin_name} 版本兼容性检查通过")
            return True, ""

        except Exception as e:
            logger.warning(f"插件 {plugin_name} 版本兼容性检查失败: {e}")
            return False, f"插件 {plugin_name} 版本兼容性检查失败: {e}"  # 检查失败时默认不允许加载

    def _show_stats(self, total_registered: int, total_failed_registration: int):
        # sourcery skip: low-code-quality
        # 获取组件统计信息
        stats = component_registry.get_registry_stats()
        action_count = stats.get("action_components", 0)
        command_count = stats.get("command_components", 0)
        total_components = stats.get("total_components", 0)

        # 📋 显示插件加载总览
        if total_registered > 0:
            logger.info("🎉 插件系统加载完成!")
            logger.info(
                f"📊 总览: {total_registered}个插件, {total_components}个组件 (Action: {action_count}, Command: {command_count})"
            )

            # 显示详细的插件列表
            logger.info("📋 已加载插件详情:")
            for plugin_name in self.loaded_plugins.keys():
                if plugin_info := component_registry.get_plugin_info(plugin_name):
                    # 插件基本信息
                    version_info = f"v{plugin_info.version}" if plugin_info.version else ""
                    author_info = f"by {plugin_info.author}" if plugin_info.author else "unknown"
                    license_info = f"[{plugin_info.license}]" if plugin_info.license else ""
                    info_parts = [part for part in [version_info, author_info, license_info] if part]
                    extra_info = f" ({', '.join(info_parts)})" if info_parts else ""

                    logger.info(f"  📦 {plugin_name}{extra_info}")

                    # Manifest信息
                    if plugin_info.manifest_data:
                        if plugin_info.keywords:
                            logger.info(f"    🏷️ 关键词: {', '.join(plugin_info.keywords)}")
                        if plugin_info.categories:
                            logger.info(f"    📁 分类: {', '.join(plugin_info.categories)}")
                        if plugin_info.homepage_url:
                            logger.info(f"    🌐 主页: {plugin_info.homepage_url}")

                    # 组件列表
                    if plugin_info.components:
                        action_components = [c for c in plugin_info.components if c.component_type.name == "ACTION"]
                        command_components = [c for c in plugin_info.components if c.component_type.name == "COMMAND"]

                        if action_components:
                            action_names = [c.name for c in action_components]
                            logger.info(f"    🎯 Action组件: {', '.join(action_names)}")

                        if command_components:
                            command_names = [c.name for c in command_components]
                            logger.info(f"    ⚡ Command组件: {', '.join(command_names)}")

                    # 依赖信息
                    if plugin_info.dependencies:
                        logger.info(f"    🔗 依赖: {', '.join(plugin_info.dependencies)}")

                    # 配置文件信息
                    if plugin_info.config_file:
                        config_status = "✅" if self.plugin_paths.get(plugin_name) else "❌"
                        logger.info(f"    ⚙️ 配置: {plugin_info.config_file} {config_status}")

            # 显示目录统计
            logger.info("📂 加载目录统计:")
            for directory in self.plugin_directories:
                if os.path.exists(directory):
                    plugins_in_dir = []
                    for plugin_name in self.loaded_plugins.keys():
                        plugin_path = self.plugin_paths.get(plugin_name, "")
                        if plugin_path.startswith(directory):
                            plugins_in_dir.append(plugin_name)

                    if plugins_in_dir:
                        logger.info(f"  📁 {directory}: {len(plugins_in_dir)}个插件 ({', '.join(plugins_in_dir)})")
                    else:
                        logger.info(f"  📁 {directory}: 0个插件")

            # 失败信息
            if total_failed_registration > 0:
                logger.info(f"⚠️  失败统计: {total_failed_registration}个插件加载失败")
                for failed_plugin, error in self.failed_plugins.items():
                    logger.info(f"  ❌ {failed_plugin}: {error}")
        else:
            logger.warning("😕 没有成功加载任何插件")

    def _show_plugin_components(self, plugin_name: str) -> None:
        if plugin_info := component_registry.get_plugin_info(plugin_name):
            component_types = {}
            for comp in plugin_info.components:
                comp_type = comp.component_type.name
                component_types[comp_type] = component_types.get(comp_type, 0) + 1

            components_str = ", ".join([f"{count}个{ctype}" for ctype, count in component_types.items()])

            # 显示manifest信息
            manifest_info = ""
            if plugin_info.license:
                manifest_info += f" [{plugin_info.license}]"
            if plugin_info.keywords:
                manifest_info += f" 关键词: {', '.join(plugin_info.keywords[:3])}"  # 只显示前3个关键词
                if len(plugin_info.keywords) > 3:
                    manifest_info += "..."

            logger.info(
                f"✅ 插件加载成功: {plugin_name} v{plugin_info.version} ({components_str}){manifest_info} - {plugin_info.description}"
            )
        else:
            logger.info(f"✅ 插件加载成功: {plugin_name}")
