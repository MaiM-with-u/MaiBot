from abc import ABC, abstractmethod
from typing import Dict, List, Type, Optional, Any, Union
import os
import inspect
import toml
import json
from src.common.logger import get_logger
from src.plugin_system.base.component_types import (
    PluginInfo,
    ComponentInfo,
    PythonDependency,
)
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system.core.component_registry import component_registry

logger = get_logger("base_plugin")

# 全局插件类注册表
_plugin_classes: Dict[str, Type["BasePlugin"]] = {}


class BasePlugin(ABC):
    """插件基类

    所有插件都应该继承这个基类，一个插件可以包含多种组件：
    - Action组件：处理聊天中的动作
    - Command组件：处理命令请求
    - 未来可扩展：Scheduler、Listener等
    """

    # 插件基本信息（子类必须定义）
    plugin_name: str = ""  # 插件内部标识符（如 "doubao_pic_plugin"）
    enable_plugin: bool = False  # 是否启用插件
    dependencies: List[str] = []  # 依赖的其他插件
    python_dependencies: List[PythonDependency] = []  # Python包依赖
    config_file_name: Optional[str] = None  # 配置文件名

    # manifest文件相关
    manifest_file_name: str = "_manifest.json"  # manifest文件名
    manifest_data: Dict[str, Any] = {}  # manifest数据

    # 配置定义
    config_schema: Dict[str, Union[Dict[str, ConfigField], str]] = {}
    config_section_descriptions: Dict[str, str] = {}

    def __init__(self, plugin_dir: str = None):
        """初始化插件

        Args:
            plugin_dir: 插件目录路径，由插件管理器传递
        """
        self.config: Dict[str, Any] = {}  # 插件配置
        self.plugin_dir = plugin_dir  # 插件目录路径
        self.log_prefix = f"[Plugin:{self.plugin_name}]"

        # 加载manifest文件
        self._load_manifest()

        # 验证插件信息
        self._validate_plugin_info()

        # 加载插件配置
        self._load_plugin_config()

        # 从manifest获取显示信息
        self.display_name = self.get_manifest_info("name", self.plugin_name)
        self.plugin_version = self.get_manifest_info("version", "1.0.0")
        self.plugin_description = self.get_manifest_info("description", "")
        self.plugin_author = self._get_author_name()

        # 创建插件信息对象
        self.plugin_info = PluginInfo(
            name=self.display_name,  # 使用显示名称
            description=self.plugin_description,
            version=self.plugin_version,
            author=self.plugin_author,
            enabled=self.enable_plugin,
            is_built_in=False,
            config_file=self.config_file_name or "",
            dependencies=self.dependencies.copy(),
            python_dependencies=self.python_dependencies.copy(),
            # manifest相关信息
            manifest_data=self.manifest_data.copy(),
            license=self.get_manifest_info("license", ""),
            homepage_url=self.get_manifest_info("homepage_url", ""),
            repository_url=self.get_manifest_info("repository_url", ""),
            keywords=self.get_manifest_info("keywords", []).copy() if self.get_manifest_info("keywords") else [],
            categories=self.get_manifest_info("categories", []).copy() if self.get_manifest_info("categories") else [],
            min_host_version=self.get_manifest_info("host_application.min_version", ""),
            max_host_version=self.get_manifest_info("host_application.max_version", ""),
        )

        logger.debug(f"{self.log_prefix} 插件基类初始化完成")

    def _validate_plugin_info(self):
        """验证插件基本信息"""
        if not self.plugin_name:
            raise ValueError(f"插件类 {self.__class__.__name__} 必须定义 plugin_name")

        # 验证manifest中的必需信息
        if not self.get_manifest_info("name"):
            raise ValueError(f"插件 {self.plugin_name} 的manifest中缺少name字段")
        if not self.get_manifest_info("description"):
            raise ValueError(f"插件 {self.plugin_name} 的manifest中缺少description字段")

    def _load_manifest(self):
        """加载manifest文件（强制要求）"""
        if not self.plugin_dir:
            raise ValueError(f"{self.log_prefix} 没有插件目录路径，无法加载manifest")

        manifest_path = os.path.join(self.plugin_dir, self.manifest_file_name)

        if not os.path.exists(manifest_path):
            error_msg = f"{self.log_prefix} 缺少必需的manifest文件: {manifest_path}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                self.manifest_data = json.load(f)

            logger.debug(f"{self.log_prefix} 成功加载manifest文件: {manifest_path}")

            # 验证manifest格式
            self._validate_manifest()

            # 从manifest覆盖插件基本信息（如果插件类中未定义）
            self._apply_manifest_overrides()

        except json.JSONDecodeError as e:
            error_msg = f"{self.log_prefix} manifest文件格式错误: {e}"
            logger.error(error_msg)
            raise ValueError(error_msg)  # noqa
        except IOError as e:
            error_msg = f"{self.log_prefix} 读取manifest文件失败: {e}"
            logger.error(error_msg)
            raise IOError(error_msg)  # noqa

    def _apply_manifest_overrides(self):
        """从manifest文件覆盖插件信息（现在只处理内部标识符的fallback）"""
        if not self.manifest_data:
            return

        # 只有当插件类中没有定义plugin_name时，才从manifest中获取作为fallback
        if not self.plugin_name:
            self.plugin_name = self.manifest_data.get("name", "").replace(" ", "_").lower()

    def _get_author_name(self) -> str:
        """从manifest获取作者名称"""
        author_info = self.get_manifest_info("author", {})
        if isinstance(author_info, dict):
            return author_info.get("name", "")
        else:
            return str(author_info) if author_info else ""

    def _validate_manifest(self):
        """验证manifest文件格式（使用强化的验证器）"""
        if not self.manifest_data:
            return

        # 导入验证器
        from src.plugin_system.utils.manifest_utils import ManifestValidator

        validator = ManifestValidator()
        is_valid = validator.validate_manifest(self.manifest_data)

        # 记录验证结果
        if validator.validation_errors or validator.validation_warnings:
            report = validator.get_validation_report()
            logger.info(f"{self.log_prefix} Manifest验证结果:\n{report}")

        # 如果有验证错误，抛出异常
        if not is_valid:
            error_msg = f"{self.log_prefix} Manifest文件验证失败"
            if validator.validation_errors:
                error_msg += f": {'; '.join(validator.validation_errors)}"
            raise ValueError(error_msg)

    def _generate_default_manifest(self, manifest_path: str):
        """生成默认的manifest文件"""
        if not self.plugin_name:
            logger.debug(f"{self.log_prefix} 插件名称未定义，无法生成默认manifest")
            return

        # 从plugin_name生成友好的显示名称
        display_name = self.plugin_name.replace("_", " ").title()

        default_manifest = {
            "manifest_version": 1,
            "name": display_name,
            "version": "1.0.0",
            "description": "插件描述",
            "author": {"name": "Unknown", "url": ""},
            "license": "MIT",
            "host_application": {"min_version": "1.0.0", "max_version": "4.0.0"},
            "keywords": [],
            "categories": [],
            "default_locale": "zh-CN",
            "locales_path": "_locales",
        }

        try:
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(default_manifest, f, ensure_ascii=False, indent=2)
            logger.info(f"{self.log_prefix} 已生成默认manifest文件: {manifest_path}")
        except IOError as e:
            logger.error(f"{self.log_prefix} 保存默认manifest文件失败: {e}")

    def get_manifest_info(self, key: str, default: Any = None) -> Any:
        """获取manifest信息

        Args:
            key: 信息键，支持点分割的嵌套键（如 "author.name"）
            default: 默认值

        Returns:
            Any: 对应的值
        """
        if not self.manifest_data:
            return default

        keys = key.split(".")
        value = self.manifest_data

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def _generate_and_save_default_config(self, config_file_path: str):
        """根据插件的Schema生成并保存默认配置文件"""
        if not self.config_schema:
            logger.debug(f"{self.log_prefix} 插件未定义config_schema，不生成配置文件")
            return

        toml_str = f"# {self.plugin_name} - 自动生成的配置文件\n"
        plugin_description = self.get_manifest_info("description", "插件配置文件")
        toml_str += f"# {plugin_description}\n\n"

        # 遍历每个配置节
        for section, fields in self.config_schema.items():
            # 添加节描述
            if section in self.config_section_descriptions:
                toml_str += f"# {self.config_section_descriptions[section]}\n"

            toml_str += f"[{section}]\n\n"

            # 遍历节内的字段
            if isinstance(fields, dict):
                for field_name, field in fields.items():
                    if isinstance(field, ConfigField):
                        # 添加字段描述
                        toml_str += f"# {field.description}"
                        if field.required:
                            toml_str += " (必需)"
                        toml_str += "\n"

                        # 如果有示例值，添加示例
                        if field.example:
                            toml_str += f"# 示例: {field.example}\n"

                        # 如果有可选值，添加说明
                        if field.choices:
                            choices_str = ", ".join(map(str, field.choices))
                            toml_str += f"# 可选值: {choices_str}\n"

                        # 添加字段值
                        value = field.default
                        if isinstance(value, str):
                            toml_str += f'{field_name} = "{value}"\n'
                        elif isinstance(value, bool):
                            toml_str += f"{field_name} = {str(value).lower()}\n"
                        else:
                            toml_str += f"{field_name} = {value}\n"

                        toml_str += "\n"
            toml_str += "\n"

        try:
            with open(config_file_path, "w", encoding="utf-8") as f:
                f.write(toml_str)
            logger.info(f"{self.log_prefix} 已生成默认配置文件: {config_file_path}")
        except IOError as e:
            logger.error(f"{self.log_prefix} 保存默认配置文件失败: {e}", exc_info=True)

    def _get_expected_config_version(self) -> str:
        """获取插件期望的配置版本号"""
        # 从config_schema的plugin.config_version字段获取
        if "plugin" in self.config_schema and isinstance(self.config_schema["plugin"], dict):
            config_version_field = self.config_schema["plugin"].get("config_version")
            if isinstance(config_version_field, ConfigField):
                return config_version_field.default
        return "1.0.0"

    def _get_current_config_version(self, config: Dict[str, Any]) -> str:
        """从配置文件中获取当前版本号"""
        if "plugin" in config and "config_version" in config["plugin"]:
            return str(config["plugin"]["config_version"])
        # 如果没有config_version字段，视为最早的版本
        return "0.0.0"

    def _backup_config_file(self, config_file_path: str) -> str:
        """备份配置文件"""
        import shutil
        import datetime

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{config_file_path}.backup_{timestamp}"

        try:
            shutil.copy2(config_file_path, backup_path)
            logger.info(f"{self.log_prefix} 配置文件已备份到: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"{self.log_prefix} 备份配置文件失败: {e}")
            return ""

    def _migrate_config_values(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> Dict[str, Any]:
        """将旧配置值迁移到新配置结构中

        Args:
            old_config: 旧配置数据
            new_config: 基于新schema生成的默认配置

        Returns:
            Dict[str, Any]: 迁移后的配置
        """

        def migrate_section(
            old_section: Dict[str, Any], new_section: Dict[str, Any], section_name: str
        ) -> Dict[str, Any]:
            """迁移单个配置节"""
            result = new_section.copy()

            for key, value in old_section.items():
                if key in new_section:
                    # 特殊处理：config_version字段总是使用新版本
                    if section_name == "plugin" and key == "config_version":
                        # 保持新的版本号，不迁移旧值
                        logger.debug(
                            f"{self.log_prefix} 更新配置版本: {section_name}.{key} = {result[key]} (旧值: {value})"
                        )
                        continue

                    # 键存在于新配置中，复制值
                    if isinstance(value, dict) and isinstance(new_section[key], dict):
                        # 递归处理嵌套字典
                        result[key] = migrate_section(value, new_section[key], f"{section_name}.{key}")
                    else:
                        result[key] = value
                        logger.debug(f"{self.log_prefix} 迁移配置: {section_name}.{key} = {value}")
                else:
                    # 键在新配置中不存在，记录警告
                    logger.warning(f"{self.log_prefix} 配置项 {section_name}.{key} 在新版本中已被移除")

            return result

        migrated_config = {}

        # 迁移每个配置节
        for section_name, new_section_data in new_config.items():
            if (
                section_name in old_config
                and isinstance(old_config[section_name], dict)
                and isinstance(new_section_data, dict)
            ):
                migrated_config[section_name] = migrate_section(
                    old_config[section_name], new_section_data, section_name
                )
            else:
                # 新增的节或类型不匹配，使用默认值
                migrated_config[section_name] = new_section_data
                if section_name in old_config:
                    logger.warning(f"{self.log_prefix} 配置节 {section_name} 结构已改变，使用默认值")

        # 检查旧配置中是否有新配置没有的节
        for section_name in old_config.keys():
            if section_name not in migrated_config:
                logger.warning(f"{self.log_prefix} 配置节 {section_name} 在新版本中已被移除")

        return migrated_config

    def _generate_config_from_schema(self) -> Dict[str, Any]:
        """根据schema生成配置数据结构（不写入文件）"""
        if not self.config_schema:
            return {}

        config_data = {}

        # 遍历每个配置节
        for section, fields in self.config_schema.items():
            if isinstance(fields, dict):
                section_data = {}

                # 遍历节内的字段
                for field_name, field in fields.items():
                    if isinstance(field, ConfigField):
                        section_data[field_name] = field.default

                config_data[section] = section_data

        return config_data

    def _save_config_to_file(self, config_data: Dict[str, Any], config_file_path: str):
        """将配置数据保存为TOML文件（包含注释）"""
        if not self.config_schema:
            logger.debug(f"{self.log_prefix} 插件未定义config_schema，不生成配置文件")
            return

        toml_str = f"# {self.plugin_name} - 配置文件\n"
        plugin_description = self.get_manifest_info("description", "插件配置文件")
        toml_str += f"# {plugin_description}\n"

        # 获取当前期望的配置版本
        expected_version = self._get_expected_config_version()
        toml_str += f"# 配置版本: {expected_version}\n\n"

        # 遍历每个配置节
        for section, fields in self.config_schema.items():
            # 添加节描述
            if section in self.config_section_descriptions:
                toml_str += f"# {self.config_section_descriptions[section]}\n"

            toml_str += f"[{section}]\n\n"

            # 遍历节内的字段
            if isinstance(fields, dict) and section in config_data:
                section_data = config_data[section]

                for field_name, field in fields.items():
                    if isinstance(field, ConfigField):
                        # 添加字段描述
                        toml_str += f"# {field.description}"
                        if field.required:
                            toml_str += " (必需)"
                        toml_str += "\n"

                        # 如果有示例值，添加示例
                        if field.example:
                            toml_str += f"# 示例: {field.example}\n"

                        # 如果有可选值，添加说明
                        if field.choices:
                            choices_str = ", ".join(map(str, field.choices))
                            toml_str += f"# 可选值: {choices_str}\n"

                        # 添加字段值（使用迁移后的值）
                        value = section_data.get(field_name, field.default)
                        if isinstance(value, str):
                            toml_str += f'{field_name} = "{value}"\n'
                        elif isinstance(value, bool):
                            toml_str += f"{field_name} = {str(value).lower()}\n"
                        elif isinstance(value, list):
                            # 格式化列表
                            if all(isinstance(item, str) for item in value):
                                formatted_list = "[" + ", ".join(f'"{item}"' for item in value) + "]"
                            else:
                                formatted_list = str(value)
                            toml_str += f"{field_name} = {formatted_list}\n"
                        else:
                            toml_str += f"{field_name} = {value}\n"

                        toml_str += "\n"
            toml_str += "\n"

        try:
            with open(config_file_path, "w", encoding="utf-8") as f:
                f.write(toml_str)
            logger.info(f"{self.log_prefix} 配置文件已保存: {config_file_path}")
        except IOError as e:
            logger.error(f"{self.log_prefix} 保存配置文件失败: {e}", exc_info=True)

    def _load_plugin_config(self):
        """加载插件配置文件，支持版本检查和自动迁移"""
        if not self.config_file_name:
            logger.debug(f"{self.log_prefix} 未指定配置文件，跳过加载")
            return

        # 优先使用传入的插件目录路径
        if self.plugin_dir:
            plugin_dir = self.plugin_dir
        else:
            # fallback：尝试从类的模块信息获取路径
            try:
                plugin_module_path = inspect.getfile(self.__class__)
                plugin_dir = os.path.dirname(plugin_module_path)
            except (TypeError, OSError):
                # 最后的fallback：从模块的__file__属性获取
                module = inspect.getmodule(self.__class__)
                if module and hasattr(module, "__file__") and module.__file__:
                    plugin_dir = os.path.dirname(module.__file__)
                else:
                    logger.warning(f"{self.log_prefix} 无法获取插件目录路径，跳过配置加载")
                    return

        config_file_path = os.path.join(plugin_dir, self.config_file_name)

        # 如果配置文件不存在，生成默认配置
        if not os.path.exists(config_file_path):
            logger.info(f"{self.log_prefix} 配置文件 {config_file_path} 不存在，将生成默认配置。")
            self._generate_and_save_default_config(config_file_path)

        if not os.path.exists(config_file_path):
            logger.warning(f"{self.log_prefix} 配置文件 {config_file_path} 不存在且无法生成。")
            return

        file_ext = os.path.splitext(self.config_file_name)[1].lower()

        if file_ext == ".toml":
            # 加载现有配置
            with open(config_file_path, "r", encoding="utf-8") as f:
                existing_config = toml.load(f) or {}

            # 检查配置版本
            current_version = self._get_current_config_version(existing_config)

            # 如果配置文件没有版本信息，跳过版本检查
            if current_version == "0.0.0":
                logger.debug(f"{self.log_prefix} 配置文件无版本信息，跳过版本检查")
                self.config = existing_config
            else:
                expected_version = self._get_expected_config_version()

                if current_version != expected_version:
                    logger.info(
                        f"{self.log_prefix} 检测到配置版本需要更新: 当前=v{current_version}, 期望=v{expected_version}"
                    )

                    # 生成新的默认配置结构
                    new_config_structure = self._generate_config_from_schema()

                    # 迁移旧配置值到新结构
                    migrated_config = self._migrate_config_values(existing_config, new_config_structure)

                    # 保存迁移后的配置
                    self._save_config_to_file(migrated_config, config_file_path)

                    logger.info(f"{self.log_prefix} 配置文件已从 v{current_version} 更新到 v{expected_version}")

                    self.config = migrated_config
                else:
                    logger.debug(f"{self.log_prefix} 配置版本匹配 (v{current_version})，直接加载")
                    self.config = existing_config

            logger.debug(f"{self.log_prefix} 配置已从 {config_file_path} 加载")

            # 从配置中更新 enable_plugin
            if "plugin" in self.config and "enabled" in self.config["plugin"]:
                self.enable_plugin = self.config["plugin"]["enabled"]
                logger.debug(f"{self.log_prefix} 从配置更新插件启用状态: {self.enable_plugin}")
        else:
            logger.warning(f"{self.log_prefix} 不支持的配置文件格式: {file_ext}，仅支持 .toml")
            self.config = {}

    @abstractmethod
    def get_plugin_components(self) -> List[tuple[ComponentInfo, Type]]:
        """获取插件包含的组件列表

        子类必须实现此方法，返回组件信息和组件类的列表

        Returns:
            List[tuple[ComponentInfo, Type]]: [(组件信息, 组件类), ...]
        """
        pass

    def register_plugin(self) -> bool:
        """注册插件及其所有组件"""

        components = self.get_plugin_components()

        # 检查依赖
        if not self._check_dependencies():
            logger.error(f"{self.log_prefix} 依赖检查失败，跳过注册")
            return False

        # 注册所有组件
        registered_components = []
        for component_info, component_class in components:
            component_info.plugin_name = self.plugin_name
            if component_registry.register_component(component_info, component_class):
                registered_components.append(component_info)
            else:
                logger.warning(f"{self.log_prefix} 组件 {component_info.name} 注册失败")

        # 更新插件信息中的组件列表
        self.plugin_info.components = registered_components

        # 注册插件
        if component_registry.register_plugin(self.plugin_info):
            logger.debug(f"{self.log_prefix} 插件注册成功，包含 {len(registered_components)} 个组件")
            return True
        else:
            logger.error(f"{self.log_prefix} 插件注册失败")
            return False

    def _check_dependencies(self) -> bool:
        """检查插件依赖"""
        if not self.dependencies:
            return True

        for dep in self.dependencies:
            if not component_registry.get_plugin_info(dep):
                logger.error(f"{self.log_prefix} 缺少依赖插件: {dep}")
                return False

        return True

    def get_config(self, key: str, default: Any = None) -> Any:
        """获取插件配置值，支持嵌套键访问

        Args:
            key: 配置键名，支持嵌套访问如 "section.subsection.key"
            default: 默认值

        Returns:
            Any: 配置值或默认值
        """
        # 支持嵌套键访问
        keys = key.split(".")
        current = self.config

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return default

        return current


def register_plugin(cls):
    """插件注册装饰器

    用法:
        @register_plugin
        class MyPlugin(BasePlugin):
            plugin_name = "my_plugin"
            plugin_description = "我的插件"
            ...
    """
    if not issubclass(cls, BasePlugin):
        logger.error(f"类 {cls.__name__} 不是 BasePlugin 的子类")
        return cls

    # 只是注册插件类，不立即实例化
    # 插件管理器会负责实例化和注册
    plugin_name = cls.plugin_name or cls.__name__
    _plugin_classes[plugin_name] = cls
    logger.debug(f"插件类已注册: {plugin_name}")

    return cls


def get_registered_plugin_classes() -> Dict[str, Type["BasePlugin"]]:
    """获取所有已注册的插件类"""
    return _plugin_classes.copy()


def instantiate_and_register_plugin(plugin_class: Type["BasePlugin"], plugin_dir: str = None) -> bool:
    """实例化并注册插件

    Args:
        plugin_class: 插件类
        plugin_dir: 插件目录路径

    Returns:
        bool: 是否成功
    """
    try:
        plugin_instance = plugin_class(plugin_dir=plugin_dir)
        return plugin_instance.register_plugin()
    except Exception as e:
        logger.error(f"注册插件 {plugin_class.__name__} 时出错: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return False
