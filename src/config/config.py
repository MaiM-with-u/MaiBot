import os
from dataclasses import field, dataclass

import tomlkit
import shutil
from datetime import datetime

from tomlkit import TOMLDocument
from tomlkit.items import Table

from src.common.logger_manager import get_logger
from rich.traceback import install

from src.config.config_base import ConfigBase
from src.config.official_configs import (
    BotConfig,
    PersonalityConfig,
    IdentityConfig,
    ExpressionConfig,
    ChatConfig,
    NormalChatConfig,
    FocusChatConfig,
    EmojiConfig,
    MemoryConfig,
    MoodConfig,
    KeywordReactionConfig,
    ChineseTypoConfig,
    ResponseSplitterConfig,
    TelemetryConfig,
    ExperimentalConfig,
    ModelConfig,
    FocusChatProcessorConfig,
    MessageReceiveConfig,
    MaimMessageConfig,
    RelationshipConfig,
)

install(extra_lines=3)


# 配置主程序日志格式
logger = get_logger("config")

CONFIG_DIR = "config"
TEMPLATE_DIR = "template"

# 考虑到，实际上配置文件中的mai_version是不会自动更新的,所以采用硬编码
# 对该字段的更新，请严格参照语义化版本规范：https://semver.org/lang/zh-CN/
MMC_VERSION = "0.7.0-snapshot.2"


def update_config():
    # 获取根目录路径
    old_config_dir = f"{CONFIG_DIR}/old"

    # 定义文件路径
    template_path = f"{TEMPLATE_DIR}/bot_config_template.toml"
    old_config_path = f"{CONFIG_DIR}/bot_config.toml"
    new_config_path = f"{CONFIG_DIR}/bot_config.toml"

    # 检查配置文件是否存在
    if not os.path.exists(old_config_path):
        logger.info("配置文件不存在，从模板创建新配置")
        os.makedirs(CONFIG_DIR, exist_ok=True)  # 创建文件夹
        shutil.copy2(template_path, old_config_path)  # 复制模板文件
        logger.info(f"已创建新配置文件，请填写后重新运行: {old_config_path}")
        # 如果是新创建的配置文件,直接返回
        quit()

    # 读取旧配置文件和模板文件
    with open(old_config_path, "r", encoding="utf-8") as f:
        old_config = tomlkit.load(f)
    with open(template_path, "r", encoding="utf-8") as f:
        new_config = tomlkit.load(f)

    # 检查version是否相同
    if old_config and "inner" in old_config and "inner" in new_config:
        old_version = old_config["inner"].get("version")
        new_version = new_config["inner"].get("version")
        if old_version and new_version and old_version == new_version:
            logger.info(f"检测到配置文件版本号相同 (v{old_version})，跳过更新")
            return
        else:
            logger.info(f"检测到版本号不同: 旧版本 v{old_version} -> 新版本 v{new_version}")
    else:
        logger.info("已有配置文件未检测到版本号，可能是旧版本。将进行更新")

    # 创建old目录（如果不存在）
    os.makedirs(old_config_dir, exist_ok=True)

    # 生成带时间戳的新文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    old_backup_path = f"{old_config_dir}/bot_config_{timestamp}.toml"

    # 移动旧配置文件到old目录
    shutil.move(old_config_path, old_backup_path)
    logger.info(f"已备份旧配置文件到: {old_backup_path}")

    # 复制模板文件到配置目录
    shutil.copy2(template_path, new_config_path)
    logger.info(f"已创建新配置文件: {new_config_path}")

    def update_dict(target: TOMLDocument | dict, source: TOMLDocument | dict):
        """
        将source字典的值更新到target字典中（如果target中存在相同的键）
        """
        for key, value in source.items():
            # 跳过version字段的更新
            if key == "version":
                continue
            if key in target:
                if isinstance(value, dict) and isinstance(target[key], (dict, Table)):
                    update_dict(target[key], value)
                else:
                    try:
                        # 对数组类型进行特殊处理
                        if isinstance(value, list):
                            # 如果是空数组，确保它保持为空数组
                            target[key] = tomlkit.array(str(value)) if value else tomlkit.array()
                        else:
                            # 其他类型使用item方法创建新值
                            target[key] = tomlkit.item(value)
                    except (TypeError, ValueError):
                        # 如果转换失败，直接赋值
                        target[key] = value

    # 将旧配置的值更新到新配置中
    logger.info("开始合并新旧配置...")
    update_dict(new_config, old_config)

    # 保存更新后的配置（保留注释和格式）
    with open(new_config_path, "w", encoding="utf-8") as f:
        f.write(tomlkit.dumps(new_config))
    logger.info("配置文件更新完成，建议检查新配置文件中的内容，以免丢失重要信息")
    quit()


@dataclass
class Config(ConfigBase):
    """总配置类"""

    MMC_VERSION: str = field(default=MMC_VERSION, repr=False, init=False)  # 硬编码的版本信息

    bot: BotConfig
    personality: PersonalityConfig
    identity: IdentityConfig
    relationship: RelationshipConfig
    chat: ChatConfig
    message_receive: MessageReceiveConfig
    normal_chat: NormalChatConfig
    focus_chat: FocusChatConfig
    focus_chat_processor: FocusChatProcessorConfig
    emoji: EmojiConfig
    expression: ExpressionConfig
    memory: MemoryConfig
    mood: MoodConfig
    keyword_reaction: KeywordReactionConfig
    chinese_typo: ChineseTypoConfig
    response_splitter: ResponseSplitterConfig
    telemetry: TelemetryConfig
    experimental: ExperimentalConfig
    model: ModelConfig
    maim_message: MaimMessageConfig


def load_config(config_path: str) -> Config:
    """
    加载配置文件
    :param config_path: 配置文件路径
    :return: Config对象
    """
    # 读取配置文件
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = tomlkit.load(f)

    # 创建Config对象
    try:
        return Config.from_dict(config_data)
    except Exception as e:
        logger.critical("配置文件解析失败")
        raise e


# 获取配置文件路径
logger.info(f"MaiCore当前版本: {MMC_VERSION}")
update_config()

logger.info("正在品鉴配置文件...")
global_config = load_config(config_path=f"{CONFIG_DIR}/bot_config.toml")
logger.info("非常的新鲜，非常的美味！")
