import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from dateutil import tz

import tomli
import tomlkit
import shutil
from datetime import datetime
from pathlib import Path
from packaging import version
from packaging.version import Version, InvalidVersion
from packaging.specifiers import SpecifierSet, InvalidSpecifier

from src.common.logger_manager import get_logger
from rich.traceback import install

install(extra_lines=3)


# 配置主程序日志格式
logger = get_logger("config")

# 考虑到，实际上配置文件中的mai_version是不会自动更新的,所以采用硬编码
is_test = True
mai_version_main = "0.6.4"
mai_version_fix = "snapshot-1"

if mai_version_fix:
    if is_test:
        mai_version = f"test-{mai_version_main}-{mai_version_fix}"
    else:
        mai_version = f"{mai_version_main}-{mai_version_fix}"
else:
    if is_test:
        mai_version = f"test-{mai_version_main}"
    else:
        mai_version = mai_version_main


def update_config():
    # 获取根目录路径
    root_dir = Path(__file__).parent.parent.parent
    template_dir = root_dir / "template"
    config_dir = root_dir / "config"
    old_config_dir = config_dir / "old"

    # 定义文件路径
    template_path = template_dir / "bot_config_template.toml"
    old_config_path = config_dir / "bot_config.toml"
    new_config_path = config_dir / "bot_config.toml"

    # 检查配置文件是否存在
    if not old_config_path.exists():
        logger.info("配置文件不存在，从模板创建新配置")
        # 创建文件夹
        old_config_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template_path, old_config_path)
        logger.info(f"已创建新配置文件，请填写后重新运行: {old_config_path}")
        # 如果是新创建的配置文件,直接返回
        return quit()

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

    # 创建old目录（如果不存在）
    old_config_dir.mkdir(exist_ok=True)

    # 生成带时间戳的新文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    old_backup_path = old_config_dir / f"bot_config_{timestamp}.toml"

    # 移动旧配置文件到old目录
    shutil.move(old_config_path, old_backup_path)
    logger.info(f"已备份旧配置文件到: {old_backup_path}")

    # 复制模板文件到配置目录
    shutil.copy2(template_path, new_config_path)
    logger.info(f"已创建新配置文件: {new_config_path}")

    # 递归更新配置
    def update_dict(target, source):
        for key, value in source.items():
            # 跳过version字段的更新
            if key == "version":
                continue
            if key in target:
                if isinstance(value, dict) and isinstance(target[key], (dict, tomlkit.items.Table)):
                    update_dict(target[key], value)
                else:
                    try:
                        # 对数组类型进行特殊处理
                        if isinstance(value, list):
                            # 如果是空数组，确保它保持为空数组
                            if not value:
                                target[key] = tomlkit.array()
                            else:
                                target[key] = tomlkit.array(value)
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
    logger.info("配置文件更新完成")


@dataclass
class BotConfig:
    """机器人配置类"""

    INNER_VERSION: Version = None
    MAI_VERSION: str = mai_version  # 硬编码的版本信息

    # bot
    BOT_QQ: Optional[str] = "114514"
    BOT_NICKNAME: Optional[str] = None
    BOT_ALIAS_NAMES: List[str] = field(default_factory=list)  # 别名，可以通过这个叫它

    # group
    talk_allowed_groups = set()
    talk_frequency_down_groups = set()
    ban_user_id = set()

    # personality
    personality_core = "用一句话或几句话描述人格的核心特点"  # 建议20字以内，谁再写3000字小作文敲谁脑袋
    personality_sides: List[str] = field(
        default_factory=lambda: [
            "用一句话或几句话描述人格的一些侧面",
            "用一句话或几句话描述人格的一些侧面",
            "用一句话或几句话描述人格的一些侧面",
        ]
    )
    personality_detail_level: int = (
        0  # 人设消息注入 prompt 详细等级 (0: 采用默认配置, 1: 核心/随机细节, 2: 核心+随机侧面/全部细节, 3: 全部)
    )
    expression_style = "描述麦麦说话的表达风格，表达习惯"
    enable_expression_learner: bool = True  # 是否启用新发言习惯注入，关闭则启用旧方法
    # identity
    identity_detail: List[str] = field(
        default_factory=lambda: [
            "身份特点",
            "身份特点",
        ]
    )
    height: int = 170  # 身高 单位厘米
    weight: int = 50  # 体重 单位千克
    age: int = 20  # 年龄 单位岁
    gender: str = "男"  # 性别
    appearance: str = "用几句话描述外貌特征"  # 外貌特征

    # schedule
    ENABLE_SCHEDULE_GEN: bool = False  # 是否启用日程生成
    PROMPT_SCHEDULE_GEN = "无日程"
    SCHEDULE_DOING_UPDATE_INTERVAL: int = 300  # 日程表更新间隔 单位秒
    SCHEDULE_TEMPERATURE: float = 0.5  # 日程表温度，建议0.5-1.0
    TIME_ZONE: str = "Asia/Shanghai"  # 时区

    # chat
    allow_focus_mode: bool = True  # 是否允许专注聊天状态

    base_normal_chat_num: int = 3  # 最多允许多少个群进行普通聊天
    base_focused_chat_num: int = 2  # 最多允许多少个群进行专注聊天
    allow_remove_duplicates: bool = True  # 是否开启心流去重（如果发现心流截断问题严重可尝试关闭）

    observation_context_size: int = 12  # 心流观察到的最长上下文大小，超过这个值的上下文会被压缩

    message_buffer: bool = True  # 消息缓冲器

    ban_words = set()
    ban_msgs_regex = set()

    # focus_chat
    reply_trigger_threshold: float = 3.0  # 心流聊天触发阈值，越低越容易触发
    default_decay_rate_per_second: float = 0.98  # 默认衰减率，越大衰减越慢
    consecutive_no_reply_threshold = 3

    compressed_length: int = 5  # 不能大于observation_context_size,心流上下文压缩的最短压缩长度，超过心流观察到的上下文长度，会压缩，最短压缩长度为5
    compress_length_limit: int = 5  # 最多压缩份数，超过该数值的压缩上下文会被删除

    # normal_chat
    model_reasoning_probability: float = 0.7  # 麦麦回答时选择推理模型(主要)模型概率
    model_normal_probability: float = 0.3  # 麦麦回答时选择一般模型(次要)模型概率

    emoji_chance: float = 0.2  # 发送表情包的基础概率
    thinking_timeout: int = 120  # 思考时间

    willing_mode: str = "classical"  # 意愿模式
    response_willing_amplifier: float = 1.0  # 回复意愿放大系数
    response_interested_rate_amplifier: float = 1.0  # 回复兴趣度放大系数
    down_frequency_rate: float = 3  # 降低回复频率的群组回复意愿降低系数
    emoji_response_penalty: float = 0.0  # 表情包回复惩罚
    mentioned_bot_inevitable_reply: bool = False  # 提及 bot 必然回复
    at_bot_inevitable_reply: bool = False  # @bot 必然回复

    # emoji
    max_emoji_num: int = 200  # 表情包最大数量
    max_reach_deletion: bool = True  # 开启则在达到最大数量时删除表情包，关闭则不会继续收集表情包
    EMOJI_CHECK_INTERVAL: int = 120  # 表情包检查间隔（分钟）

    save_pic: bool = False  # 是否保存图片
    save_emoji: bool = False  # 是否保存表情包
    steal_emoji: bool = True  # 是否偷取表情包，让麦麦可以发送她保存的这些表情包

    EMOJI_CHECK: bool = False  # 是否开启过滤
    EMOJI_CHECK_PROMPT: str = "符合公序良俗"  # 表情包过滤要求

    # memory
    build_memory_interval: int = 600  # 记忆构建间隔（秒）
    memory_build_distribution: list = field(
        default_factory=lambda: [4, 2, 0.6, 24, 8, 0.4]
    )  # 记忆构建分布，参数：分布1均值，标准差，权重，分布2均值，标准差，权重
    build_memory_sample_num: int = 10  # 记忆构建采样数量
    build_memory_sample_length: int = 20  # 记忆构建采样长度
    memory_compress_rate: float = 0.1  # 记忆压缩率

    forget_memory_interval: int = 600  # 记忆遗忘间隔（秒）
    memory_forget_time: int = 24  # 记忆遗忘时间（小时）
    memory_forget_percentage: float = 0.01  # 记忆遗忘比例

    consolidate_memory_interval: int = 1000  # 记忆整合间隔（秒）
    consolidation_similarity_threshold: float = 0.7  # 相似度阈值
    consolidate_memory_percentage: float = 0.01  # 检查节点比例

    memory_ban_words: list = field(
        default_factory=lambda: ["表情包", "图片", "回复", "聊天记录"]
    )  # 添加新的配置项默认值

    long_message_auto_truncate: bool = True  # HFC 模式过长消息自动截断，防止他人 prompt 恶意注入，减少token消耗，但可能损失图片/长文信息，按需选择状态（默认开启）

    # mood
    mood_update_interval: float = 1.0  # 情绪更新间隔 单位秒
    mood_decay_rate: float = 0.95  # 情绪衰减率
    mood_intensity_factor: float = 0.7  # 情绪强度因子

    # keywords
    keywords_reaction_rules = []  # 关键词回复规则

    # chinese_typo
    chinese_typo_enable = True  # 是否启用中文错别字生成器
    chinese_typo_error_rate = 0.03  # 单字替换概率
    chinese_typo_min_freq = 7  # 最小字频阈值
    chinese_typo_tone_error_rate = 0.2  # 声调错误概率
    chinese_typo_word_replace_rate = 0.02  # 整词替换概率

    # response_splitter
    enable_kaomoji_protection = False  # 是否启用颜文字保护
    enable_response_splitter = True  # 是否启用回复分割器
    response_max_length = 100  # 回复允许的最大长度
    response_max_sentence_num = 3  # 回复允许的最大句子数

    model_max_output_length: int = 800  # 最大回复长度

    # remote
    remote_enable: bool = True  # 是否启用远程控制

    # experimental
    enable_Legacy_HFC: bool = False  # 是否启用旧 HFC 处理器
    enable_friend_chat: bool = True  # 是否启用好友聊天
    # enable_think_flow: bool = False  # 是否启用思考流程
    enable_friend_whitelist: bool = True  # 是否启用好友白名单
    talk_allowed_private = set()
    rename_person: bool = (
        True  # 是否启用改名工具，可以让麦麦对唯一名进行更改，可能可以更拟人地称呼他人，但是也可能导致记忆混淆的问题
    )

    # pfc
    enable_pfc_chatting: bool = True  # 是否启用PFC聊天，该功能仅作用于私聊，与回复模式独立
    pfc_message_buffer_size: int = (
        2  # PFC 聊天消息缓冲数量，有利于使聊天节奏更加紧凑流畅，请根据实际 LLM 响应速度进行调整，默认2条
    )
    pfc_recent_history_display_count: int = 18  # PFC 对话最大可见上下文

    # pfc.checker
    enable_pfc_reply_checker: bool = True  # 是否启用 PFC 的回复检查器
    pfc_max_reply_attempts: int = 3  # 发言最多尝试次数
    pfc_max_chat_history_for_checker: int = 30  # checker聊天记录最大可见上文长度

    # pfc.emotion
    pfc_emotion_update_intensity: float = 0.6  # 情绪更新强度
    pfc_emotion_history_count: int = 5  # 情绪更新最大可见上下文长度

    # pfc.relationship
    pfc_relationship_incremental_interval: int = 10  # 关系值增值强度
    pfc_relationship_incremental_msg_count: int = 10  # 会话中，关系值判断最大可见上下文
    pfc_relationship_incremental_default_change: float = (
        1.0  # 会话中，关系值默认更新值（当 llm 返回错误时默认采用该值）
    )
    pfc_relationship_incremental_max_change: float = 5.0  # 会话中，关系值最大可变值
    pfc_relationship_final_msg_count: int = 30  # 会话结束时，关系值判断最大可见上下文
    pfc_relationship_final_default_change: float = 5.0  # 会话结束时，关系值默认更新值
    pfc_relationship_final_max_change: float = 50.0  # 会话结束时，关系值最大可变值

    # pfc.fallback
    pfc_historical_fallback_exclude_seconds: int = 45  # pfc 翻看聊天记录排除最近时长

    # pfc.idle_chat
    enable_idle_chat: bool = True  # 是否启用 pfc 主动发言
    idle_check_interval: int = 10  # 检查间隔，10分钟检查一次
    min_cooldown: int = 7200  # 最短冷却时间，2小时 (7200秒)
    max_cooldown: int = 18000  # 最长冷却时间，5小时 (18000秒)

    # Group Nickname
    enable_nickname_mapping: bool = False  # 绰号映射功能总开关
    max_nicknames_in_prompt: int = 10  # Prompt 中最多注入的绰号数量
    nickname_probability_smoothing: int = 1  # 绰号加权随机选择的平滑因子
    nickname_queue_max_size: int = 100  # 绰号处理队列最大容量
    nickname_process_sleep_interval: float = 5  # 绰号处理进程休眠间隔（秒）
    nickname_analysis_history_limit: int = 30  # 绰号处理可见最大上下文
    nickname_analysis_probability: float = 0.1  # 绰号随机概率命中，该值越大，绰号分析越频繁

    # 模型配置
    llm_reasoning: dict[str, str] = field(default_factory=lambda: {})
    # llm_reasoning_minor: dict[str, str] = field(default_factory=lambda: {})
    llm_normal: Dict[str, str] = field(default_factory=lambda: {})
    llm_topic_judge: Dict[str, str] = field(default_factory=lambda: {})
    llm_summary: Dict[str, str] = field(default_factory=lambda: {})
    embedding: Dict[str, str] = field(default_factory=lambda: {})
    vlm: Dict[str, str] = field(default_factory=lambda: {})
    moderation: Dict[str, str] = field(default_factory=lambda: {})

    llm_observation: Dict[str, str] = field(default_factory=lambda: {})
    llm_sub_heartflow: Dict[str, str] = field(default_factory=lambda: {})
    llm_heartflow: Dict[str, str] = field(default_factory=lambda: {})
    llm_tool_use: Dict[str, str] = field(default_factory=lambda: {})
    llm_plan: Dict[str, str] = field(default_factory=lambda: {})
    llm_nickname_mapping: Dict[str, str] = field(default_factory=lambda: {})
    llm_scheduler_all: Dict[str, str] = field(default_factory=lambda: {})
    llm_scheduler_doing: Dict[str, str] = field(default_factory=lambda: {})
    llm_PFC_relationship_eval: Dict[str, str] = field(default_factory=lambda: {})

    api_urls: Dict[str, str] = field(default_factory=lambda: {})

    @staticmethod
    def get_config_dir() -> str:
        """获取配置文件目录"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
        config_dir = os.path.join(root_dir, "config")
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
        return config_dir

    @classmethod
    def convert_to_specifierset(cls, value: str) -> SpecifierSet:
        """将 字符串 版本表达式转换成 SpecifierSet
        Args:
            value[str]: 版本表达式(字符串)
        Returns:
            SpecifierSet
        """

        try:
            converted = SpecifierSet(value)
        except InvalidSpecifier:
            logger.error(f"{value} 分类使用了错误的版本约束表达式\n", "请阅读 https://semver.org/lang/zh-CN/ 修改代码")
            exit(1)

        return converted

    @classmethod
    def get_config_version(cls, toml: dict) -> Version:
        """提取配置文件的 SpecifierSet 版本数据
        Args:
            toml[dict]: 输入的配置文件字典
        Returns:
            Version
        """

        if "inner" in toml:
            try:
                config_version: str = toml["inner"]["version"]
            except KeyError as e:
                logger.error("配置文件中 inner 段 不存在, 这是错误的配置文件")
                raise KeyError(f"配置文件中 inner 段 不存在 {e}, 这是错误的配置文件") from e
        else:
            toml["inner"] = {"version": "0.0.0"}
            config_version = toml["inner"]["version"]

        try:
            ver = version.parse(config_version)
        except InvalidVersion as e:
            logger.error(
                "配置文件中 inner段 的 version 键是错误的版本描述\n"
                "请阅读 https://semver.org/lang/zh-CN/ 修改配置，并参考本项目指定的模板进行修改\n"
                "本项目在不同的版本下有不同的模板，请注意识别"
            )
            raise InvalidVersion("配置文件中 inner段 的 version 键是错误的版本描述\n") from e

        return ver

    @classmethod
    def load_config(cls, config_path: str = None) -> "BotConfig":
        """从TOML配置文件加载配置"""
        config = cls()

        def personality(parent: dict):
            personality_config = parent["personality"]
            if config.INNER_VERSION in SpecifierSet(">=1.2.4"):
                config.personality_core = personality_config.get("personality_core", config.personality_core)
                config.personality_sides = personality_config.get("personality_sides", config.personality_sides)
            if config.INNER_VERSION in SpecifierSet(">=1.7.1"):
                config.personality_detail_level = personality_config.get(
                    "personality_detail_level", config.personality_sides
                )
            if config.INNER_VERSION in SpecifierSet(">=1.7.0"):
                config.expression_style = personality_config.get("expression_style", config.expression_style)
            if config.INNER_VERSION in SpecifierSet(">=1.7.1"):
                config.enable_expression_learner = personality_config.get(
                    "enable_expression_learner", config.enable_expression_learner
                )

        def identity(parent: dict):
            identity_config = parent["identity"]
            if config.INNER_VERSION in SpecifierSet(">=1.2.4"):
                config.identity_detail = identity_config.get("identity_detail", config.identity_detail)
                config.height = identity_config.get("height", config.height)
                config.weight = identity_config.get("weight", config.weight)
                config.age = identity_config.get("age", config.age)
                config.gender = identity_config.get("gender", config.gender)
                config.appearance = identity_config.get("appearance", config.appearance)

        def schedule(parent: dict):
            schedule_config = parent["schedule"]
            config.ENABLE_SCHEDULE_GEN = schedule_config.get("enable_schedule_gen", config.ENABLE_SCHEDULE_GEN)
            config.PROMPT_SCHEDULE_GEN = schedule_config.get("prompt_schedule_gen", config.PROMPT_SCHEDULE_GEN)
            config.SCHEDULE_DOING_UPDATE_INTERVAL = schedule_config.get(
                "schedule_doing_update_interval", config.SCHEDULE_DOING_UPDATE_INTERVAL
            )
            logger.info(
                f"载入自定义日程prompt:{schedule_config.get('prompt_schedule_gen', config.PROMPT_SCHEDULE_GEN)}"
            )
            if config.INNER_VERSION in SpecifierSet(">=1.0.2"):
                config.SCHEDULE_TEMPERATURE = schedule_config.get("schedule_temperature", config.SCHEDULE_TEMPERATURE)
                time_zone = schedule_config.get("time_zone", config.TIME_ZONE)
                if tz.gettz(time_zone) is None:
                    logger.error(f"无效的时区: {time_zone}，使用默认值: {config.TIME_ZONE}")
                else:
                    config.TIME_ZONE = time_zone

        def emoji(parent: dict):
            emoji_config = parent["emoji"]
            config.EMOJI_CHECK_INTERVAL = emoji_config.get("check_interval", config.EMOJI_CHECK_INTERVAL)
            config.EMOJI_CHECK_PROMPT = emoji_config.get("check_prompt", config.EMOJI_CHECK_PROMPT)
            config.EMOJI_CHECK = emoji_config.get("enable_check", config.EMOJI_CHECK)
            if config.INNER_VERSION in SpecifierSet(">=1.1.1"):
                config.max_emoji_num = emoji_config.get("max_emoji_num", config.max_emoji_num)
                config.max_reach_deletion = emoji_config.get("max_reach_deletion", config.max_reach_deletion)
            if config.INNER_VERSION in SpecifierSet(">=1.4.2"):
                config.save_pic = emoji_config.get("save_pic", config.save_pic)
                config.save_emoji = emoji_config.get("save_emoji", config.save_emoji)
                config.steal_emoji = emoji_config.get("steal_emoji", config.steal_emoji)

        def group_nickname(parent: dict):
            if config.INNER_VERSION in SpecifierSet(">=1.7.1"):
                group_nickname_config = parent.get("group_nickname", {})
                config.enable_nickname_mapping = group_nickname_config.get(
                    "enable_nickname_mapping", config.enable_nickname_mapping
                )
                config.max_nicknames_in_prompt = group_nickname_config.get(
                    "max_nicknames_in_prompt", config.max_nicknames_in_prompt
                )
                config.nickname_probability_smoothing = group_nickname_config.get(
                    "nickname_probability_smoothing", config.nickname_probability_smoothing
                )
                config.nickname_queue_max_size = group_nickname_config.get(
                    "nickname_queue_max_size", config.nickname_queue_max_size
                )
                config.nickname_process_sleep_interval = group_nickname_config.get(
                    "nickname_process_sleep_interval", config.nickname_process_sleep_interval
                )
                config.nickname_analysis_history_limit = group_nickname_config.get(
                    "nickname_analysis_history_limit", config.nickname_analysis_history_limit
                )
                config.nickname_analysis_probability = group_nickname_config.get(
                    "nickname_analysis_probability", config.nickname_analysis_probability
                )

        def bot(parent: dict):
            # 机器人基础配置
            bot_config = parent["bot"]
            bot_qq = bot_config.get("qq")
            config.BOT_QQ = str(bot_qq)
            config.BOT_NICKNAME = bot_config.get("nickname", config.BOT_NICKNAME)
            config.BOT_ALIAS_NAMES = bot_config.get("alias_names", config.BOT_ALIAS_NAMES)

        def chat(parent: dict):
            chat_config = parent["chat"]
            config.allow_focus_mode = chat_config.get("allow_focus_mode", config.allow_focus_mode)
            config.base_normal_chat_num = chat_config.get("base_normal_chat_num", config.base_normal_chat_num)
            config.base_focused_chat_num = chat_config.get("base_focused_chat_num", config.base_focused_chat_num)
            config.observation_context_size = chat_config.get(
                "observation_context_size", config.observation_context_size
            )
            config.message_buffer = chat_config.get("message_buffer", config.message_buffer)
            config.ban_words = chat_config.get("ban_words", config.ban_words)
            for r in chat_config.get("ban_msgs_regex", config.ban_msgs_regex):
                config.ban_msgs_regex.add(re.compile(r))
            if config.INNER_VERSION in SpecifierSet(">=1.7.1"):
                config.allow_remove_duplicates = chat_config.get(
                    "allow_remove_duplicates", config.allow_remove_duplicates
                )

        def normal_chat(parent: dict):
            normal_chat_config = parent["normal_chat"]
            config.model_reasoning_probability = normal_chat_config.get(
                "model_reasoning_probability", config.model_reasoning_probability
            )
            config.model_normal_probability = normal_chat_config.get(
                "model_normal_probability", config.model_normal_probability
            )
            config.emoji_chance = normal_chat_config.get("emoji_chance", config.emoji_chance)
            config.thinking_timeout = normal_chat_config.get("thinking_timeout", config.thinking_timeout)

            config.willing_mode = normal_chat_config.get("willing_mode", config.willing_mode)
            config.response_willing_amplifier = normal_chat_config.get(
                "response_willing_amplifier", config.response_willing_amplifier
            )
            config.response_interested_rate_amplifier = normal_chat_config.get(
                "response_interested_rate_amplifier", config.response_interested_rate_amplifier
            )
            config.down_frequency_rate = normal_chat_config.get("down_frequency_rate", config.down_frequency_rate)
            config.emoji_response_penalty = normal_chat_config.get(
                "emoji_response_penalty", config.emoji_response_penalty
            )

            config.mentioned_bot_inevitable_reply = normal_chat_config.get(
                "mentioned_bot_inevitable_reply", config.mentioned_bot_inevitable_reply
            )
            config.at_bot_inevitable_reply = normal_chat_config.get(
                "at_bot_inevitable_reply", config.at_bot_inevitable_reply
            )

        def focus_chat(parent: dict):
            focus_chat_config = parent["focus_chat"]
            config.compressed_length = focus_chat_config.get("compressed_length", config.compressed_length)
            config.compress_length_limit = focus_chat_config.get("compress_length_limit", config.compress_length_limit)
            config.reply_trigger_threshold = focus_chat_config.get(
                "reply_trigger_threshold", config.reply_trigger_threshold
            )
            config.default_decay_rate_per_second = focus_chat_config.get(
                "default_decay_rate_per_second", config.default_decay_rate_per_second
            )
            config.consecutive_no_reply_threshold = focus_chat_config.get(
                "consecutive_no_reply_threshold", config.consecutive_no_reply_threshold
            )

        def model(parent: dict):
            # 加载模型配置
            model_config: dict = parent["model"]

            config_list = [
                "llm_reasoning",
                # "llm_reasoning_minor",
                "llm_normal",
                "llm_topic_judge",
                "llm_summary",
                "vlm",
                "embedding",
                "llm_tool_use",
                "llm_observation",
                "llm_sub_heartflow",
                "llm_plan",
                "llm_heartflow",
                "llm_PFC_action_planner",
                "llm_PFC_chat",
                "llm_nickname_mapping",
                "llm_scheduler_all",
                "llm_scheduler_doing",
                "llm_PFC_relationship_eval",
            ]

            for item in config_list:
                if item in model_config:
                    cfg_item: dict = model_config[item]

                    # base_url 的例子： SILICONFLOW_BASE_URL
                    # key 的例子： SILICONFLOW_KEY
                    cfg_target = {
                        "name": "",
                        "base_url": "",
                        "key": "",
                        "stream": False,
                        "pri_in": 0,
                        "pri_out": 0,
                        "temp": 0.7,
                    }

                    if config.INNER_VERSION in SpecifierSet("<=0.0.0"):
                        cfg_target = cfg_item

                    elif config.INNER_VERSION in SpecifierSet(">=0.0.1"):
                        stable_item = ["name", "pri_in", "pri_out"]

                        stream_item = ["stream"]
                        if config.INNER_VERSION in SpecifierSet(">=1.0.1"):
                            stable_item.append("stream")

                        pricing_item = ["pri_in", "pri_out"]

                        # 从配置中原始拷贝稳定字段
                        for i in stable_item:
                            # 如果 字段 属于计费项 且获取不到，那默认值是 0
                            if i in pricing_item and i not in cfg_item:
                                cfg_target[i] = 0

                            if i in stream_item and i not in cfg_item:
                                cfg_target[i] = False

                            else:
                                # 没有特殊情况则原样复制
                                try:
                                    cfg_target[i] = cfg_item[i]
                                except KeyError as e:
                                    logger.error(f"{item} 中的必要字段不存在，请检查")
                                    raise KeyError(f"{item} 中的必要字段 {e} 不存在，请检查") from e

                        # 如果配置中有temp参数，就使用配置中的值
                        if "temp" in cfg_item:
                            cfg_target["temp"] = cfg_item["temp"]
                        else:
                            # 如果没有temp参数，就删除默认值
                            cfg_target.pop("temp", None)

                        provider = cfg_item.get("provider")
                        if provider is None:
                            logger.error(f"provider 字段在模型配置 {item} 中不存在，请检查")
                            raise KeyError(f"provider 字段在模型配置 {item} 中不存在，请检查")

                        cfg_target["base_url"] = f"{provider}_BASE_URL"
                        cfg_target["key"] = f"{provider}_KEY"

                    # 如果 列表中的项目在 model_config 中，利用反射来设置对应项目
                    setattr(config, item, cfg_target)
                else:
                    logger.error(f"模型 {item} 在config中不存在，请检查，或尝试更新配置文件")
                    raise KeyError(f"模型 {item} 在config中不存在，请检查，或尝试更新配置文件")

        def memory(parent: dict):
            memory_config = parent["memory"]
            config.build_memory_interval = memory_config.get("build_memory_interval", config.build_memory_interval)
            config.forget_memory_interval = memory_config.get("forget_memory_interval", config.forget_memory_interval)
            config.memory_ban_words = set(memory_config.get("memory_ban_words", []))
            config.memory_forget_time = memory_config.get("memory_forget_time", config.memory_forget_time)
            config.memory_forget_percentage = memory_config.get(
                "memory_forget_percentage", config.memory_forget_percentage
            )
            config.memory_compress_rate = memory_config.get("memory_compress_rate", config.memory_compress_rate)
            if config.INNER_VERSION in SpecifierSet(">=0.0.11"):
                config.memory_build_distribution = memory_config.get(
                    "memory_build_distribution", config.memory_build_distribution
                )
                config.build_memory_sample_num = memory_config.get(
                    "build_memory_sample_num", config.build_memory_sample_num
                )
                config.build_memory_sample_length = memory_config.get(
                    "build_memory_sample_length", config.build_memory_sample_length
                )
            if config.INNER_VERSION in SpecifierSet(">=1.5.1"):
                config.consolidate_memory_interval = memory_config.get(
                    "consolidate_memory_interval", config.consolidate_memory_interval
                )
                config.consolidation_similarity_threshold = memory_config.get(
                    "consolidation_similarity_threshold", config.consolidation_similarity_threshold
                )
                config.consolidate_memory_percentage = memory_config.get(
                    "consolidate_memory_percentage", config.consolidate_memory_percentage
                )
            if config.INNER_VERSION in SpecifierSet(">=1.7.1"):
                config.long_message_auto_truncate = memory_config.get(
                    "long_message_auto_truncate", config.long_message_auto_truncate
                )

        def remote(parent: dict):
            remote_config = parent["remote"]
            config.remote_enable = remote_config.get("enable", config.remote_enable)

        def mood(parent: dict):
            mood_config = parent["mood"]
            config.mood_update_interval = mood_config.get("mood_update_interval", config.mood_update_interval)
            config.mood_decay_rate = mood_config.get("mood_decay_rate", config.mood_decay_rate)
            config.mood_intensity_factor = mood_config.get("mood_intensity_factor", config.mood_intensity_factor)

        def keywords_reaction(parent: dict):
            keywords_reaction_config = parent["keywords_reaction"]
            if keywords_reaction_config.get("enable", False):
                config.keywords_reaction_rules = keywords_reaction_config.get("rules", config.keywords_reaction_rules)
                for rule in config.keywords_reaction_rules:
                    if rule.get("enable", False) and "regex" in rule:
                        rule["regex"] = [re.compile(r) for r in rule.get("regex", [])]

        def chinese_typo(parent: dict):
            chinese_typo_config = parent["chinese_typo"]
            config.chinese_typo_enable = chinese_typo_config.get("enable", config.chinese_typo_enable)
            config.chinese_typo_error_rate = chinese_typo_config.get("error_rate", config.chinese_typo_error_rate)
            config.chinese_typo_min_freq = chinese_typo_config.get("min_freq", config.chinese_typo_min_freq)
            config.chinese_typo_tone_error_rate = chinese_typo_config.get(
                "tone_error_rate", config.chinese_typo_tone_error_rate
            )
            config.chinese_typo_word_replace_rate = chinese_typo_config.get(
                "word_replace_rate", config.chinese_typo_word_replace_rate
            )

        def response_splitter(parent: dict):
            response_splitter_config = parent["response_splitter"]
            config.enable_response_splitter = response_splitter_config.get(
                "enable_response_splitter", config.enable_response_splitter
            )
            config.response_max_length = response_splitter_config.get("response_max_length", config.response_max_length)
            config.response_max_sentence_num = response_splitter_config.get(
                "response_max_sentence_num", config.response_max_sentence_num
            )
            if config.INNER_VERSION in SpecifierSet(">=1.4.2"):
                config.enable_kaomoji_protection = response_splitter_config.get(
                    "enable_kaomoji_protection", config.enable_kaomoji_protection
                )
            if config.INNER_VERSION in SpecifierSet(">=1.6.0"):
                config.model_max_output_length = response_splitter_config.get(
                    "model_max_output_length", config.model_max_output_length
                )

        def groups(parent: dict):
            groups_config = parent["groups"]
            # config.talk_allowed_groups = set(groups_config.get("talk_allowed", []))
            config.talk_allowed_groups = set(str(group) for group in groups_config.get("talk_allowed", []))
            # config.talk_frequency_down_groups = set(groups_config.get("talk_frequency_down", []))
            config.talk_frequency_down_groups = set(
                str(group) for group in groups_config.get("talk_frequency_down", [])
            )
            # config.ban_user_id = set(groups_config.get("ban_user_id", []))
            config.ban_user_id = set(str(user) for user in groups_config.get("ban_user_id", []))

        def experimental(parent: dict):
            experimental_config = parent["experimental"]
            config.enable_friend_chat = experimental_config.get("enable_friend_chat", config.enable_friend_chat)
            # config.enable_think_flow = experimental_config.get("enable_think_flow", config.enable_think_flow)
            config.talk_allowed_private = set(str(user) for user in experimental_config.get("talk_allowed_private", []))
            if config.INNER_VERSION in SpecifierSet(">=1.7.1"):
                config.enable_friend_whitelist = experimental_config.get(
                    "enable_friend_whitelist", config.enable_friend_whitelist
                )
            if config.INNER_VERSION in SpecifierSet(">=1.7.1"):
                config.rename_person = experimental_config.get("rename_person", config.rename_person)
            if config.INNER_VERSION in SpecifierSet(">=1.7.1"):
                config.enable_Legacy_HFC = experimental_config.get("enable_Legacy_HFC", config.enable_Legacy_HFC)

        def pfc(parent: dict):
            if config.INNER_VERSION in SpecifierSet(">=1.7.1"):
                pfc_config = parent.get("pfc", {})
                # 解析 [pfc] 下的直接字段
                config.enable_pfc_chatting = pfc_config.get("enable_pfc_chatting", config.enable_pfc_chatting)
                config.pfc_message_buffer_size = pfc_config.get(
                    "pfc_message_buffer_size", config.pfc_message_buffer_size
                )
                config.pfc_recent_history_display_count = pfc_config.get(
                    "pfc_recent_history_display_count", config.pfc_recent_history_display_count
                )

                # 解析 [[pfc.checker]] 子表
                checker_list = pfc_config.get("checker", [])
                if checker_list and isinstance(checker_list, list):
                    checker_config = checker_list[0] if checker_list else {}
                    config.enable_pfc_reply_checker = checker_config.get(
                        "enable_pfc_reply_checker", config.enable_pfc_reply_checker
                    )
                    config.pfc_max_reply_attempts = checker_config.get(
                        "pfc_max_reply_attempts", config.pfc_max_reply_attempts
                    )
                    config.pfc_max_chat_history_for_checker = checker_config.get(
                        "pfc_max_chat_history_for_checker", config.pfc_max_chat_history_for_checker
                    )

                # 解析 [[pfc.emotion]] 子表
                emotion_list = pfc_config.get("emotion", [])
                if emotion_list and isinstance(emotion_list, list):
                    emotion_config = emotion_list[0] if emotion_list else {}
                    config.pfc_emotion_update_intensity = emotion_config.get(
                        "pfc_emotion_update_intensity", config.pfc_emotion_update_intensity
                    )
                    config.pfc_emotion_history_count = emotion_config.get(
                        "pfc_emotion_history_count", config.pfc_emotion_history_count
                    )

                # 解析 [[pfc.relationship]] 子表
                relationship_list = pfc_config.get("relationship", [])
                if relationship_list and isinstance(relationship_list, list):
                    relationship_config = relationship_list[0] if relationship_list else {}
                    config.pfc_relationship_incremental_interval = relationship_config.get(
                        "pfc_relationship_incremental_interval", config.pfc_relationship_incremental_interval
                    )
                    config.pfc_relationship_incremental_msg_count = relationship_config.get(
                        "pfc_relationship_incremental_msg_count", config.pfc_relationship_incremental_msg_count
                    )
                    config.pfc_relationship_incremental_default_change = relationship_config.get(
                        "pfc_relationship_incremental_default_change",
                        config.pfc_relationship_incremental_default_change,
                    )
                    config.pfc_relationship_incremental_max_change = relationship_config.get(
                        "pfc_relationship_incremental_max_change", config.pfc_relationship_incremental_max_change
                    )
                    config.pfc_relationship_final_msg_count = relationship_config.get(
                        "pfc_relationship_final_msg_count", config.pfc_relationship_final_msg_count
                    )
                    config.pfc_relationship_final_default_change = relationship_config.get(
                        "pfc_relationship_final_default_change", config.pfc_relationship_final_default_change
                    )
                    config.pfc_relationship_final_max_change = relationship_config.get(
                        "pfc_relationship_final_max_change", config.pfc_relationship_final_max_change
                    )

                # 解析 [[pfc.fallback]] 子表
                fallback_list = pfc_config.get("fallback", [])
                if fallback_list and isinstance(fallback_list, list):
                    fallback_config = fallback_list[0] if fallback_list else {}
                    config.pfc_historical_fallback_exclude_seconds = fallback_config.get(
                        "pfc_historical_fallback_exclude_seconds", config.pfc_historical_fallback_exclude_seconds
                    )

                # 解析 [[pfc.idle_chat]] 子表
                idle_chat_list = pfc_config.get("idle_chat", [])
                if idle_chat_list and isinstance(idle_chat_list, list):
                    idle_chat_config = idle_chat_list[0] if idle_chat_list else {}
                    config.enable_idle_chat = idle_chat_config.get("enable_idle_chat", config.enable_idle_chat)
                    config.idle_check_interval = idle_chat_config.get("idle_check_interval", config.idle_check_interval)
                    config.min_cooldown = idle_chat_config.get("min_cooldown", config.min_cooldown)
                    config.max_cooldown = idle_chat_config.get("max_cooldown", config.max_cooldown)

        # 版本表达式：>=1.0.0,<2.0.0
        # 允许字段：func: method, support: str, notice: str, necessary: bool
        # 如果使用 notice 字段，在该组配置加载时，会展示该字段对用户的警示
        # 例如："notice": "personality 将在 1.3.2 后被移除"，那么在有效版本中的用户就会虽然可以
        # 正常执行程序，但是会看到这条自定义提示

        # 版本格式：主版本号.次版本号.修订号，版本号递增规则如下：
        #     主版本号：当你做了不兼容的 API 修改，
        #     次版本号：当你做了向下兼容的功能性新增，
        #     修订号：当你做了向下兼容的问题修正。
        # 先行版本号及版本编译信息可以加到"主版本号.次版本号.修订号"的后面，作为延伸。

        # 如果你做了break的修改，就应该改动主版本号
        # 如果做了一个兼容修改，就不应该要求这个选项是必须的！
        include_configs = {
            "bot": {"func": bot, "support": ">=0.0.0"},
            "groups": {"func": groups, "support": ">=0.0.0"},
            "personality": {"func": personality, "support": ">=0.0.0"},
            "identity": {"func": identity, "support": ">=1.2.4"},
            "schedule": {"func": schedule, "support": ">=0.0.11", "necessary": False},
            "emoji": {"func": emoji, "support": ">=0.0.0"},
            "model": {"func": model, "support": ">=0.0.0"},
            "memory": {"func": memory, "support": ">=0.0.0", "necessary": False},
            "mood": {"func": mood, "support": ">=0.0.0"},
            "remote": {"func": remote, "support": ">=0.0.10", "necessary": False},
            "keywords_reaction": {"func": keywords_reaction, "support": ">=0.0.2", "necessary": False},
            "chinese_typo": {"func": chinese_typo, "support": ">=0.0.3", "necessary": False},
            "response_splitter": {"func": response_splitter, "support": ">=0.0.11", "necessary": False},
            "experimental": {"func": experimental, "support": ">=0.0.11", "necessary": False},
            "chat": {"func": chat, "support": ">=1.6.0", "necessary": False},
            "normal_chat": {"func": normal_chat, "support": ">=1.6.0", "necessary": False},
            "focus_chat": {"func": focus_chat, "support": ">=1.6.0", "necessary": False},
            "group_nickname": {"func": group_nickname, "support": ">=1.7.1", "necessary": False},
            "pfc": {"func": pfc, "support": ">=1.7.1", "necessary": False},
        }

        # 原地修改，将 字符串版本表达式 转换成 版本对象
        for key in include_configs:
            item_support = include_configs[key]["support"]
            include_configs[key]["support"] = cls.convert_to_specifierset(item_support)

        if os.path.exists(config_path):
            with open(config_path, "rb") as f:
                try:
                    toml_dict = tomli.load(f)
                except tomli.TOMLDecodeError as e:
                    logger.critical(f"配置文件bot_config.toml填写有误，请检查第{e.lineno}行第{e.colno}处：{e.msg}")
                    exit(1)

                # 获取配置文件版本
                config.INNER_VERSION = cls.get_config_version(toml_dict)

                # 如果在配置中找到了需要的项，调用对应项的闭包函数处理
                for key in include_configs:
                    if key in toml_dict:
                        group_specifierset: SpecifierSet = include_configs[key]["support"]

                        # 检查配置文件版本是否在支持范围内
                        if config.INNER_VERSION in group_specifierset:
                            # 如果版本在支持范围内，检查是否存在通知
                            if "notice" in include_configs[key]:
                                logger.warning(include_configs[key]["notice"])

                            include_configs[key]["func"](toml_dict)

                        else:
                            # 如果版本不在支持范围内，崩溃并提示用户
                            logger.error(
                                f"配置文件中的 '{key}' 字段的版本 ({config.INNER_VERSION}) 不在支持范围内。\n"
                                f"当前程序仅支持以下版本范围: {group_specifierset}"
                            )
                            raise InvalidVersion(f"当前程序仅支持以下版本范围: {group_specifierset}")

                    # 如果 necessary 项目存在，而且显式声明是 False，进入特殊处理
                    elif "necessary" in include_configs[key] and include_configs[key].get("necessary") is False:
                        # 通过 pass 处理的项虽然直接忽略也是可以的，但是为了不增加理解困难，依然需要在这里显式处理
                        if key == "keywords_reaction":
                            pass

                    else:
                        # 如果用户根本没有需要的配置项，提示缺少配置
                        logger.error(f"配置文件中缺少必需的字段: '{key}'")
                        raise KeyError(f"配置文件中缺少必需的字段: '{key}'")

                # identity_detail字段非空检查
                if not config.identity_detail:
                    logger.error("配置文件错误：[identity] 部分的 identity_detail 不能为空字符串")
                    raise ValueError("配置文件错误：[identity] 部分的 identity_detail 不能为空字符串")

                logger.success(f"成功加载配置文件: {config_path}")

        return config


# 获取配置文件路径
logger.info(f"MaiCore当前版本: {mai_version}")
update_config()

bot_config_floder_path = BotConfig.get_config_dir()
logger.info(f"正在品鉴配置文件目录: {bot_config_floder_path}")

bot_config_path = os.path.join(bot_config_floder_path, "bot_config.toml")

if os.path.exists(bot_config_path):
    # 如果开发环境配置文件不存在，则使用默认配置文件
    logger.info(f"异常的新鲜，异常的美味: {bot_config_path}")
else:
    # 配置文件不存在
    logger.error("配置文件不存在，请检查路径: {bot_config_path}")
    raise FileNotFoundError(f"配置文件不存在: {bot_config_path}")

global_config = BotConfig.load_config(config_path=bot_config_path)
