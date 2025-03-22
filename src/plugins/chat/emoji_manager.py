import asyncio
import base64
import hashlib
import os
import random
import time
import traceback
from typing import Optional, Tuple
from PIL import Image
import io

from nonebot import get_driver

from ...common.database import db
from ..chat.config import global_config
from ..chat.utils import get_embedding
from ..chat.utils_image import ImageManager, image_path_to_base64
from ..models.utils_model import LLM_request
from src.common.logger import get_module_logger

logger = get_module_logger("emoji")


driver = get_driver()
config = driver.config
image_manager = ImageManager()


class EmojiManager:
    _instance = None
    EMOJI_DIR = os.path.join("data", "emoji")  # 表情包存储目录

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        self._scan_task = None
        self.vlm = LLM_request(model=global_config.vlm, temperature=0.3, max_tokens=1000, request_type="emoji")
        self.llm_emotion_judge = LLM_request(
            model=global_config.llm_emotion_judge, max_tokens=600, temperature=0.8, request_type="emoji"
        )  # 更高的温度，更少的token（后续可以根据情绪来调整温度）

    def _ensure_emoji_dir(self):
        """确保表情存储目录存在"""
        os.makedirs(self.EMOJI_DIR, exist_ok=True)

    def initialize(self):
        """初始化数据库连接和表情目录"""
        if not self._initialized:
            try:
                self._ensure_emoji_collection()
                self._ensure_emoji_dir()
                self._initialized = True
                # 启动时执行一次完整性检查
                self.check_emoji_file_integrity()
            except Exception:
                logger.exception("初始化表情管理器失败")

    def _ensure_db(self):
        """确保数据库已初始化"""
        if not self._initialized:
            self.initialize()
        if not self._initialized:
            raise RuntimeError("EmojiManager not initialized")

    def _ensure_emoji_collection(self):
        """确保emoji集合存在并创建索引

        这个函数用于确保MongoDB数据库中存在emoji集合,并创建必要的索引。

        索引的作用是加快数据库查询速度:
        - embedding字段的2dsphere索引: 用于加速向量相似度搜索,帮助快速找到相似的表情包
        - tags字段的普通索引: 加快按标签搜索表情包的速度
        - filename字段的唯一索引: 确保文件名不重复,同时加快按文件名查找的速度

        没有索引的话,数据库每次查询都需要扫描全部数据,建立索引后可以大大提高查询效率。
        """
        if "emoji" not in db.list_collection_names():
            db.create_collection("emoji")
            db.emoji.create_index([("embedding", "2dsphere")])
            db.emoji.create_index([("filename", 1)], unique=True)

    def record_usage(self, emoji_id: str):
        """记录表情使用次数"""
        try:
            self._ensure_db()
            db.emoji.update_one({"_id": emoji_id}, {"$inc": {"usage_count": 1}})
        except Exception as e:
            logger.error(f"记录表情使用失败: {str(e)}")

    async def get_emoji_for_text(self, text: str) -> Optional[Tuple[str, str]]:
        """根据文本内容获取相关表情包
        Args:
            text: 输入文本
        Returns:
            Optional[str]: 表情包文件路径，如果没有找到则返回None


        可不可以通过 配置文件中的指令 来自定义使用表情包的逻辑？
        我觉得可行

        """
        try:
            self._ensure_db()

            # 获取文本的embedding
            text_for_search = await self._get_kimoji_for_text(text)
            if not text_for_search:
                logger.error("无法获取文本的情绪")
                return None
            text_embedding = await get_embedding(text_for_search, request_type="emoji")
            if not text_embedding:
                logger.error("无法获取文本的embedding")
                return None

            try:
                # 获取所有表情包
                all_emojis = list(db.emoji.find({}, {"_id": 1, "path": 1, "embedding": 1, "description": 1}))

                if not all_emojis:
                    logger.warning("数据库中没有任何表情包")
                    return None

                # 计算余弦相似度并排序
                def cosine_similarity(v1, v2):
                    if not v1 or not v2:
                        return 0
                    dot_product = sum(a * b for a, b in zip(v1, v2))
                    norm_v1 = sum(a * a for a in v1) ** 0.5
                    norm_v2 = sum(b * b for b in v2) ** 0.5
                    if norm_v1 == 0 or norm_v2 == 0:
                        return 0
                    return dot_product / (norm_v1 * norm_v2)

                # 计算所有表情包与输入文本的相似度
                emoji_similarities = [
                    (emoji, cosine_similarity(text_embedding, emoji.get("embedding", []))) for emoji in all_emojis
                ]

                # 按相似度降序排序
                emoji_similarities.sort(key=lambda x: x[1], reverse=True)

                # 获取前3个最相似的表情包
                top_10_emojis = emoji_similarities[: 10 if len(emoji_similarities) > 10 else len(emoji_similarities)]

                if not top_10_emojis:
                    logger.warning("未找到匹配的表情包")
                    return None

                # 从前3个中随机选择一个
                selected_emoji, similarity = random.choice(top_10_emojis)

                if selected_emoji and "path" in selected_emoji:
                    # 更新使用次数
                    db.emoji.update_one({"_id": selected_emoji["_id"]}, {"$inc": {"usage_count": 1}})

                    logger.info(
                        f"[匹配] 找到表情包: {selected_emoji.get('description', '无描述')} (相似度: {similarity:.4f})"
                    )
                    # 稍微改一下文本描述，不然容易产生幻觉，描述已经包含 表情包 了
                    return selected_emoji["path"], "[ %s ]" % selected_emoji.get("description", "无描述")

            except Exception as search_error:
                logger.error(f"[错误] 搜索表情包失败: {str(search_error)}")
                return None

            return None

        except Exception as e:
            logger.error(f"[错误] 获取表情包失败: {str(e)}")
            return None

    async def _get_emoji_discription(self, image_base64: str) -> str:
        """获取表情包的标签，使用image_manager的描述生成功能"""

        try:
            # 使用image_manager获取描述，去掉前后的方括号和"表情包："前缀
            description = await image_manager.get_emoji_description(image_base64)
            # 去掉[表情包：xxx]的格式，只保留描述内容
            description = description.strip("[]").replace("表情包：", "")
            return description

        except Exception as e:
            logger.error(f"[错误] 获取表情包描述失败: {str(e)}")
            return None

    async def _check_emoji(self, image_base64: str, image_format: str) -> str:
        try:
            prompt = (
                f'这是一个表情包，请回答这个表情包是否满足"{global_config.EMOJI_CHECK_PROMPT}"的要求，是则回答是，'
                f"否则回答否，不要出现任何其他内容"
            )

            content, _ = await self.vlm.generate_response_for_image(prompt, image_base64, image_format)
            logger.debug(f"[检查] 表情包检查结果: {content}")
            return content

        except Exception as e:
            logger.error(f"[错误] 表情包检查失败: {str(e)}")
            return None

    async def _get_kimoji_for_text(self, text: str):
        try:
            prompt = (
                f"这是{global_config.BOT_NICKNAME}将要发送的消息内容:\n{text}\n若要为其配上表情包，"
                f"请你输出这个表情包应该表达怎样的情感，应该给人什么样的感觉，不要太简洁也不要太长，"
                f'注意不要输出任何对消息内容的分析内容，只输出"一种什么样的感觉"中间的形容词部分。'
            )

            content, _ = await self.llm_emotion_judge.generate_response_async(prompt, temperature=1.5)
            logger.info(f"[情感] 表情包情感描述: {content}")
            return content

        except Exception as e:
            logger.error(f"[错误] 获取表情包情感失败: {str(e)}")
            return None

    async def scan_new_emojis(self):
        """扫描新的表情包"""
        try:
            emoji_dir = self.EMOJI_DIR
            os.makedirs(emoji_dir, exist_ok=True)

            # 获取所有支持的图片文件
            files_to_process = [
                f for f in os.listdir(emoji_dir) if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif"))
            ]

            for filename in files_to_process:
                image_path = os.path.join(emoji_dir, filename)

                # 获取图片的base64编码和哈希值
                image_base64 = image_path_to_base64(image_path)
                if image_base64 is None:
                    os.remove(image_path)
                    continue

                image_bytes = base64.b64decode(image_base64)
                image_hash = hashlib.md5(image_bytes).hexdigest()
                image_format = Image.open(io.BytesIO(image_bytes)).format.lower()
                # 检查是否已经注册过
                existing_emoji_by_path = db["emoji"].find_one({"filename": filename})
                existing_emoji_by_hash = db["emoji"].find_one({"hash": image_hash})
                if existing_emoji_by_path and existing_emoji_by_hash:
                    if existing_emoji_by_path["_id"] != existing_emoji_by_hash["_id"]:
                        logger.error(f"[错误] 表情包已存在但记录不一致: {filename}")
                        db.emoji.delete_one({"_id": existing_emoji_by_path["_id"]})
                        db.emoji.delete_one({"_id": existing_emoji_by_hash["_id"]})
                        existing_emoji = None
                    else:
                        existing_emoji = existing_emoji_by_hash
                elif existing_emoji_by_hash:
                    logger.error(f"[错误] 表情包hash已存在但path不存在: {filename}")
                    db.emoji.delete_one({"_id": existing_emoji_by_hash["_id"]})
                    existing_emoji = None
                elif existing_emoji_by_path:
                    logger.error(f"[错误] 表情包path已存在但hash不存在: {filename}")
                    db.emoji.delete_one({"_id": existing_emoji_by_path["_id"]})
                    existing_emoji = None
                else:
                    existing_emoji = None

                description = None

                if existing_emoji:
                    # 即使表情包已存在，也检查是否需要同步到images集合
                    description = existing_emoji.get("discription")
                    # 检查是否在images集合中存在
                    existing_image = db.images.find_one({"hash": image_hash})
                    if not existing_image:
                        # 同步到images集合
                        image_doc = {
                            "hash": image_hash,
                            "path": image_path,
                            "type": "emoji",
                            "description": description,
                            "timestamp": int(time.time()),
                        }
                        db.images.update_one({"hash": image_hash}, {"$set": image_doc}, upsert=True)
                        # 保存描述到image_descriptions集合
                        image_manager._save_description_to_db(image_hash, description, "emoji")
                        logger.success(f"[同步] 已同步表情包到images集合: {filename}")
                    continue

                # 检查是否在images集合中已有描述
                existing_description = image_manager._get_description_from_db(image_hash, "emoji")

                if existing_description:
                    description = existing_description
                else:
                    # 获取表情包的描述
                    description = await self._get_emoji_discription(image_base64)

                if global_config.EMOJI_CHECK:
                    check = await self._check_emoji(image_base64, image_format)
                    if "是" not in check:
                        os.remove(image_path)
                        logger.info(f"[过滤] 表情包描述: {description}")
                        logger.info(f"[过滤] 表情包不满足规则，已移除: {check}")
                        continue
                    logger.info(f"[检查] 表情包检查通过: {check}")

                if description is not None:
                    embedding = await get_embedding(description, request_type="emoji")
                    # 准备数据库记录
                    emoji_record = {
                        "filename": filename,
                        "path": image_path,
                        "embedding": embedding,
                        "discription": description,
                        "hash": image_hash,
                        "timestamp": int(time.time()),
                    }

                    # 保存到emoji数据库
                    db["emoji"].insert_one(emoji_record)
                    logger.success(f"[注册] 新表情包: {filename}")
                    logger.info(f"[描述] {description}")

                    # 保存到images数据库
                    image_doc = {
                        "hash": image_hash,
                        "path": image_path,
                        "type": "emoji",
                        "description": description,
                        "timestamp": int(time.time()),
                    }
                    db.images.update_one({"hash": image_hash}, {"$set": image_doc}, upsert=True)
                    # 保存描述到image_descriptions集合
                    image_manager._save_description_to_db(image_hash, description, "emoji")
                    logger.success(f"[同步] 已保存到images集合: {filename}")
                else:
                    logger.warning(f"[跳过] 表情包: {filename}")

        except Exception:
            logger.exception("[错误] 扫描表情包失败")

    async def _periodic_scan(self, interval_MINS: int = 10):
        """定期扫描新表情包"""
        while True:
            logger.info("[扫描] 开始扫描新表情包...")
            await self.scan_new_emojis()
            await asyncio.sleep(interval_MINS * 60)  # 每600秒扫描一次

    def check_emoji_file_integrity(self):
        """检查表情包文件完整性
        如果文件已被删除，则从数据库中移除对应记录
        """
        try:
            self._ensure_db()
            # 获取所有表情包记录
            all_emojis = list(db.emoji.find())
            removed_count = 0
            total_count = len(all_emojis)

            for emoji in all_emojis:
                try:
                    if "path" not in emoji:
                        logger.warning(f"[检查] 发现无效记录（缺少path字段），ID: {emoji.get('_id', 'unknown')}")
                        db.emoji.delete_one({"_id": emoji["_id"]})
                        removed_count += 1
                        continue

                    if "embedding" not in emoji:
                        logger.warning(f"[检查] 发现过时记录（缺少embedding字段），ID: {emoji.get('_id', 'unknown')}")
                        db.emoji.delete_one({"_id": emoji["_id"]})
                        removed_count += 1
                        continue

                    # 检查文件是否存在
                    if not os.path.exists(emoji["path"]):
                        logger.warning(f"[检查] 表情包文件已被删除: {emoji['path']}")
                        # 从数据库中删除记录
                        result = db.emoji.delete_one({"_id": emoji["_id"]})
                        if result.deleted_count > 0:
                            logger.debug(f"[清理] 成功删除数据库记录: {emoji['_id']}")
                            removed_count += 1
                        else:
                            logger.error(f"[错误] 删除数据库记录失败: {emoji['_id']}")
                        continue

                    if "hash" not in emoji:
                        logger.warning(f"[检查] 发现缺失记录（缺少hash字段），ID: {emoji.get('_id', 'unknown')}")
                        hash = hashlib.md5(open(emoji["path"], "rb").read()).hexdigest()
                        db.emoji.update_one({"_id": emoji["_id"]}, {"$set": {"hash": hash}})
                    else:
                        file_hash = hashlib.md5(open(emoji["path"], "rb").read()).hexdigest()
                        if emoji["hash"] != file_hash:
                            logger.warning(f"[检查] 表情包文件hash不匹配，ID: {emoji.get('_id', 'unknown')}")
                            db.emoji.delete_one({"_id": emoji["_id"]})
                            removed_count += 1

                except Exception as item_error:
                    logger.error(f"[错误] 处理表情包记录时出错: {str(item_error)}")
                    continue

            # 验证清理结果
            remaining_count = db.emoji.count_documents({})
            if removed_count > 0:
                logger.success(f"[清理] 已清理 {removed_count} 个失效的表情包记录")
                logger.info(f"[统计] 清理前: {total_count} | 清理后: {remaining_count}")
            else:
                logger.info(f"[检查] 已检查 {total_count} 个表情包记录")

        except Exception as e:
            logger.error(f"[错误] 检查表情包完整性失败: {str(e)}")
            logger.error(traceback.format_exc())

    async def start_periodic_check(self, interval_MINS: int = 120):
        while True:
            self.check_emoji_file_integrity()
            await asyncio.sleep(interval_MINS * 60)


# 创建全局单例

emoji_manager = EmojiManager()
