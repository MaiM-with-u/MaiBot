import json
import os
import hashlib
import time

from src.common.logger import get_logger
from src.config.config import global_config
from src.llm_models.utils_model import LLMRequest
from src.person_info.person_info import get_person_info_manager
from rich.traceback import install

install(extra_lines=3)

logger = get_logger("individuality")


class Individuality:
    """个体特征管理类"""

    def __init__(self):
        self.name = ""
        self.bot_person_id = ""
        self.meta_info_file_path = "data/personality/meta.json"
        self.personality_data_file_path = "data/personality/personality_data.json"

        self.model = LLMRequest(
            model=global_config.model.utils,
            request_type="individuality.compress",
        )

    async def initialize(self) -> None:
        """初始化个体特征"""
        bot_nickname = global_config.bot.nickname
        personality_core = global_config.personality.personality_core
        personality_side = global_config.personality.personality_side
        identity = global_config.personality.identity


        person_info_manager = get_person_info_manager()
        self.bot_person_id = person_info_manager.get_person_id("system", "bot_id")
        self.name = bot_nickname

        # 检查配置变化，如果变化则清空
        personality_changed, identity_changed = await self._check_config_and_clear_if_changed(
            bot_nickname, personality_core, personality_side, identity
        )

        logger.info("正在构建人设信息")

        # 如果配置有变化，重新生成压缩版本
        if personality_changed or identity_changed:
            logger.info("检测到配置变化，重新生成压缩版本")
            personality_result = await self._create_personality(personality_core, personality_side)
            identity_result = await self._create_identity(identity)
        else:
            logger.info("配置未变化，使用缓存版本")
            # 从文件中获取已有的结果
            personality_result, identity_result = self._get_personality_from_file()
            if not personality_result or not identity_result:
                logger.info("未找到有效缓存，重新生成")
                personality_result = await self._create_personality(personality_core, personality_side)
                identity_result = await self._create_identity(identity)

        # 保存到文件
        if personality_result and identity_result:
            self._save_personality_to_file(personality_result, identity_result)
            logger.info("已将人设构建并保存到文件")
        else:
            logger.error("人设构建失败")

        # 如果任何一个发生变化，都需要清空数据库中的info_list（因为这影响整体人设）
        if personality_changed or identity_changed:
            logger.info("将清空数据库中原有的关键词缓存")
            update_data = {
                "platform": "system",
                "user_id": "bot_id",
                "person_name": self.name,
                "nickname": self.name,
            }
            await person_info_manager.update_one_field(self.bot_person_id, "info_list", [], data=update_data)

    async def get_personality_block(self) -> str:
        bot_name = global_config.bot.nickname
        if global_config.bot.alias_names:
            bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
        else:
            bot_nickname = ""
        
        # 从文件获取 short_impression
        personality, identity = self._get_personality_from_file()
        
        # 确保short_impression是列表格式且有足够的元素
        if not personality or not identity:
            logger.warning(f"personality或identity为空: {personality}, {identity}, 使用默认值")
            personality = "友好活泼"
            identity = "人类"
        
        prompt_personality = f"{personality}\n{identity}"
        return f"你的名字是{bot_name}{bot_nickname}，你{prompt_personality}"

    def _get_config_hash(
        self, bot_nickname: str, personality_core: str, personality_side: str, identity: str
    ) -> tuple[str, str]:
        """获取personality和identity配置的哈希值

        Returns:
            tuple: (personality_hash, identity_hash)
        """
        # 人格配置哈希
        personality_config = {
            "nickname": bot_nickname,
            "personality_core": personality_core,
            "personality_side": personality_side,
            "compress_personality": global_config.personality.compress_personality,
        }
        personality_str = json.dumps(personality_config, sort_keys=True)
        personality_hash = hashlib.md5(personality_str.encode("utf-8")).hexdigest()

        # 身份配置哈希
        identity_config = {
            "identity": identity,
            "compress_identity": global_config.personality.compress_identity,
        }
        identity_str = json.dumps(identity_config, sort_keys=True)
        identity_hash = hashlib.md5(identity_str.encode("utf-8")).hexdigest()

        return personality_hash, identity_hash

    async def _check_config_and_clear_if_changed(
        self, bot_nickname: str, personality_core: str, personality_side: str, identity: str
    ) -> tuple[bool, bool]:
        """检查配置是否发生变化，如果变化则清空相应缓存

        Returns:
            tuple: (personality_changed, identity_changed)
        """
        person_info_manager = get_person_info_manager()
        current_personality_hash, current_identity_hash = self._get_config_hash(
            bot_nickname, personality_core, personality_side, identity
        )

        meta_info = self._load_meta_info()
        stored_personality_hash = meta_info.get("personality_hash")
        stored_identity_hash = meta_info.get("identity_hash")

        personality_changed = current_personality_hash != stored_personality_hash
        identity_changed = current_identity_hash != stored_identity_hash

        if personality_changed:
            logger.info("检测到人格配置发生变化")

        if identity_changed:
            logger.info("检测到身份配置发生变化")

        # 如果任何一个发生变化，都需要清空info_list（因为这影响整体人设）
        if personality_changed or identity_changed:
            logger.info("将清空原有的关键词缓存")
            update_data = {
                "platform": "system",
                "user_id": "bot_id",
                "person_name": self.name,
                "nickname": self.name,
            }
            await person_info_manager.update_one_field(self.bot_person_id, "info_list", [], data=update_data)

        # 更新元信息文件
        new_meta_info = {
            "personality_hash": current_personality_hash,
            "identity_hash": current_identity_hash,
        }
        self._save_meta_info(new_meta_info)

        return personality_changed, identity_changed

    def _load_meta_info(self) -> dict:
        """从JSON文件中加载元信息"""
        if os.path.exists(self.meta_info_file_path):
            try:
                with open(self.meta_info_file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"读取meta_info文件失败: {e}, 将创建新文件。")
                return {}
        return {}

    def _save_meta_info(self, meta_info: dict):
        """将元信息保存到JSON文件"""
        try:
            os.makedirs(os.path.dirname(self.meta_info_file_path), exist_ok=True)
            with open(self.meta_info_file_path, "w", encoding="utf-8") as f:
                json.dump(meta_info, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"保存meta_info文件失败: {e}")

    def _load_personality_data(self) -> dict:
        """从JSON文件中加载personality数据"""
        if os.path.exists(self.personality_data_file_path):
            try:
                with open(self.personality_data_file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"读取personality_data文件失败: {e}, 将创建新文件。")
                return {}
        return {}

    def _save_personality_data(self, personality_data: dict):
        """将personality数据保存到JSON文件"""
        try:
            os.makedirs(os.path.dirname(self.personality_data_file_path), exist_ok=True)
            with open(self.personality_data_file_path, "w", encoding="utf-8") as f:
                json.dump(personality_data, f, ensure_ascii=False, indent=2)
            logger.debug(f"已保存personality数据到文件: {self.personality_data_file_path}")
        except IOError as e:
            logger.error(f"保存personality_data文件失败: {e}")

    def _get_personality_from_file(self) -> tuple[str, str]:
        """从文件获取personality数据
        
        Returns:
            tuple: (personality, identity)
        """
        personality_data = self._load_personality_data()
        personality = personality_data.get("personality", "友好活泼")
        identity = personality_data.get("identity", "人类")
        return personality, identity

    def _save_personality_to_file(self, personality: str, identity: str):
        """保存personality数据到文件
        
        Args:
            personality: 压缩后的人格描述
            identity: 压缩后的身份描述
        """
        personality_data = {
            "personality": personality,
            "identity": identity,
            "bot_nickname": self.name,
            "last_updated": int(time.time())
        }
        self._save_personality_data(personality_data)

    async def _create_personality(self, personality_core: str, personality_side: str) -> str:
        # sourcery skip: merge-list-append, move-assign
        """使用LLM创建压缩版本的impression

        Args:
            personality_core: 核心人格
            personality_side: 人格侧面列表

        Returns:
            str: 压缩后的impression文本
        """
        logger.info("正在构建人格.........")

        # 核心人格保持不变
        personality_parts = []
        if personality_core:
            personality_parts.append(f"{personality_core}")

        # 准备需要压缩的内容
        if global_config.personality.compress_personality:
            personality_to_compress = f"人格特质: {personality_side}"

            prompt = f"""请将以下人格信息进行简洁压缩，保留主要内容，用简练的中文表达：
{personality_to_compress}

要求：
1. 保持原意不变，尽量使用原文
2. 尽量简洁，不超过30字
3. 直接输出压缩后的内容，不要解释"""

            response, (_, _) = await self.model.generate_response_async(
                prompt=prompt,
            )

            if response and response.strip():
                personality_parts.append(response.strip())
                logger.info(f"精简人格侧面: {response.strip()}")
            else:
                logger.error(f"使用LLM压缩人设时出错: {response}")
                # 压缩失败时使用原始内容
                if personality_side:
                    personality_parts.append(personality_side)
            
            if personality_parts:
                personality_result = "。".join(personality_parts)
            else:
                personality_result = personality_core or "友好活泼"
        else:
            personality_result = personality_core
            if personality_side:
                personality_result += f"，{personality_side}"

        return personality_result

    async def _create_identity(self, identity: str) -> str:
        """使用LLM创建压缩版本的impression"""
        logger.info("正在构建身份.........")

        if global_config.personality.compress_identity:
            identity_to_compress = f"身份背景: {identity}"

            prompt = f"""请将以下身份信息进行简洁压缩，保留主要内容，用简练的中文表达：
{identity_to_compress}

要求：
1. 保持原意不变，尽量使用原文
2. 尽量简洁，不超过30字
3. 直接输出压缩后的内容，不要解释"""

            response, (_, _) = await self.model.generate_response_async(
                prompt=prompt,
            )

            if response and response.strip():
                identity_result = response.strip()
                logger.info(f"精简身份: {identity_result}")
            else:
                logger.error(f"使用LLM压缩身份时出错: {response}")
                identity_result = identity
        else:
            identity_result = identity

        return identity_result


individuality = None


def get_individuality():
    global individuality
    if individuality is None:
        individuality = Individuality()
    return individuality
