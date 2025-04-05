import base64
import os
import time
import hashlib
from typing import Optional
from PIL import Image
import io


from ...common.database import db
from ..config.config import global_config
from ..models.utils_model import LLM_request

from src.common.logger import get_module_logger

logger = get_module_logger("chat_image")


class ImageManager:
    _instance = None
    IMAGE_DIR = "data"  # 图像存储根目录

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._ensure_image_collection()
            self._ensure_description_collection()
            self._ensure_image_dir()
            self._initialized = True
            self._llm = LLM_request(model=global_config.vlm, temperature=0.4, max_tokens=300, request_type="image")

    def _ensure_image_dir(self):
        """确保图像存储目录存在"""
        os.makedirs(self.IMAGE_DIR, exist_ok=True)

    def _ensure_image_collection(self):
        """确保images集合存在并创建索引"""
        if "images" not in db.list_collection_names():
            db.create_collection("images")

        # 删除旧索引
        db.images.drop_indexes()
        # 创建新的复合索引
        db.images.create_index([("hash", 1), ("type", 1)], unique=True)
        db.images.create_index([("url", 1)])
        db.images.create_index([("path", 1)])

    def _ensure_description_collection(self):
        """确保image_descriptions集合存在并创建索引"""
        if "image_descriptions" not in db.list_collection_names():
            db.create_collection("image_descriptions")

        # 删除旧索引
        db.image_descriptions.drop_indexes()
        # 创建新的复合索引
        db.image_descriptions.create_index([("hash", 1), ("type", 1)], unique=True)

    def _get_description_from_db(self, image_hash: str, description_type: str) -> Optional[str]:
        """从数据库获取图片描述

        Args:
            image_hash: 图片哈希值
            description_type: 描述类型 ('emoji' 或 'image')

        Returns:
            Optional[str]: 描述文本，如果不存在则返回None
        """
        result = db.image_descriptions.find_one({"hash": image_hash, "type": description_type})
        return result["description"] if result else None

    def _save_description_to_db(self, image_hash: str, description: str, description_type: str) -> None:
        """保存图片描述到数据库

        Args:
            image_hash: 图片哈希值
            description: 描述文本
            description_type: 描述类型 ('emoji' 或 'image')
        """
        try:
            db.image_descriptions.update_one(
                {"hash": image_hash, "type": description_type},
                {
                    "$set": {
                        "description": description,
                        "timestamp": int(time.time()),
                        "hash": image_hash,  # 确保hash字段存在
                        "type": description_type,  # 确保type字段存在
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logger.error(f"保存描述到数据库失败: {str(e)}")

    async def get_emoji_description(self, image_base64: str) -> str:
        """获取表情包描述，带查重和保存功能"""
        try:
            # 计算图片哈希
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()

            # 查询缓存的描述
            cached_description = self._get_description_from_db(image_hash, "emoji")
            if cached_description:
                logger.debug(f"缓存表情包描述: {cached_description}")
                return f"[表情包：{cached_description}]"

            # 调用AI获取描述
            if image_format == "gif" or image_format == "GIF":
                image_base64 = self.transform_gif(image_base64)
                prompt = "这是一个动态图表情包，每一张图代表了动态图的某一帧，黑色背景代表透明，使用中文简洁的描述一下表情包的内容和表达的情感，简短一些"
                description, _ = await self._llm.generate_response_for_image(prompt, image_base64, "jpg")
            else:
                prompt = "这是一个表情包，使用中文简洁的描述一下表情包的内容和表情包所表达的情感"
                description, _ = await self._llm.generate_response_for_image(prompt, image_base64, image_format)

            cached_description = self._get_description_from_db(image_hash, "emoji")
            if cached_description:
                logger.warning(f"虽然生成了描述，但是找到缓存表情包描述: {cached_description}")
                return f"[表情包：{cached_description}]"

            # 根据配置决定是否保存图片
            if global_config.EMOJI_SAVE:
                # 生成文件名和路径
                timestamp = int(time.time())
                filename = f"{timestamp}_{image_hash[:8]}.{image_format}"
                if not os.path.exists(os.path.join(self.IMAGE_DIR, "emoji")):
                    os.makedirs(os.path.join(self.IMAGE_DIR, "emoji"))
                file_path = os.path.join(self.IMAGE_DIR, "emoji", filename)

                try:
                    # 保存文件
                    with open(file_path, "wb") as f:
                        f.write(image_bytes)

                    # 保存到数据库
                    image_doc = {
                        "hash": image_hash,
                        "path": file_path,
                        "type": "emoji",
                        "description": description,
                        "timestamp": timestamp,
                    }
                    db.images.update_one({"hash": image_hash}, {"$set": image_doc}, upsert=True)
                    logger.success(f"保存表情包: {file_path}")
                except Exception as e:
                    logger.error(f"保存表情包文件失败: {str(e)}")

            # 保存描述到数据库
            self._save_description_to_db(image_hash, description, "emoji")

            return f"[表情包：{description}]"
        except Exception as e:
            logger.error(f"获取表情包描述失败: {str(e)}")
            return "[表情包]"

    async def get_image_description(self, image_base64: str) -> str:
        """获取普通图片描述，带查重和保存功能"""
        try:
            # 计算图片哈希
            image_bytes = base64.b64decode(image_base64)
            image_hash = hashlib.md5(image_bytes).hexdigest()
            image_format = Image.open(io.BytesIO(image_bytes)).format.lower()

            # 查询缓存的描述
            cached_description = self._get_description_from_db(image_hash, "image")
            if cached_description:
                logger.debug(f"图片描述缓存中 {cached_description}")
                return f"[图片：{cached_description}]"

            # 调用AI获取描述
            prompt = (
                "请用中文描述这张图片的内容。如果有文字，请把文字都描述出来。并尝试猜测这个图片的含义。最多100个字。"
            )
            description, _ = await self._llm.generate_response_for_image(prompt, image_base64, image_format)

            cached_description = self._get_description_from_db(image_hash, "image")
            if cached_description:
                logger.warning(f"虽然生成了描述，但是找到缓存图片描述 {cached_description}")
                return f"[图片：{cached_description}]"

            logger.debug(f"描述是{description}")

            if description is None:
                logger.warning("AI未能生成图片描述")
                return "[图片]"

            # 根据配置决定是否保存图片
            if global_config.EMOJI_SAVE:
                # 生成文件名和路径
                timestamp = int(time.time())
                filename = f"{timestamp}_{image_hash[:8]}.{image_format}"
                if not os.path.exists(os.path.join(self.IMAGE_DIR, "image")):
                    os.makedirs(os.path.join(self.IMAGE_DIR, "image"))
                file_path = os.path.join(self.IMAGE_DIR, "image", filename)

                try:
                    # 保存文件
                    with open(file_path, "wb") as f:
                        f.write(image_bytes)

                    # 保存到数据库
                    image_doc = {
                        "hash": image_hash,
                        "path": file_path,
                        "type": "image",
                        "description": description,
                        "timestamp": timestamp,
                    }
                    db.images.update_one({"hash": image_hash}, {"$set": image_doc}, upsert=True)
                    logger.success(f"保存图片: {file_path}")
                except Exception as e:
                    logger.error(f"保存图片文件失败: {str(e)}")

            # 保存描述到数据库
            self._save_description_to_db(image_hash, description, "image")

            return f"[图片：{description}]"
        except Exception as e:
            logger.error(f"获取图片描述失败: {str(e)}")
            return "[图片]"

    def transform_gif(self, gif_base64: str) -> str:
        """将GIF转换为水平拼接的静态图像

        Args:
            gif_base64: GIF的base64编码字符串

        Returns:
            str: 拼接后的JPG图像的base64编码字符串
        """
        try:
            # 解码base64
            gif_data = base64.b64decode(gif_base64)
            gif = Image.open(io.BytesIO(gif_data))
            
            # 收集所有帧
            frames = []
            try:
                while True:
                    gif.seek(len(frames))
                    frame = gif.convert('RGB')
                    frames.append(frame.copy())
            except EOFError:
                pass

            if not frames:
                raise ValueError("No frames found in GIF")

            # 计算需要抽取的帧的索引
            total_frames = len(frames)
            if total_frames <= 15:
                selected_frames = frames
            else:
                # 均匀抽取10帧
                indices = [int(i * (total_frames - 1) / 14) for i in range(15)]
                selected_frames = [frames[i] for i in indices]

            # 获取单帧的尺寸
            frame_width, frame_height = selected_frames[0].size
            
            # 计算目标尺寸，保持宽高比
            target_height = 200  # 固定高度
            target_width = int((target_height / frame_height) * frame_width)
            
            # 调整所有帧的大小
            resized_frames = [frame.resize((target_width, target_height), Image.Resampling.LANCZOS) 
                             for frame in selected_frames]

            # 创建拼接图像
            total_width = target_width * len(resized_frames)
            combined_image = Image.new('RGB', (total_width, target_height))

            # 水平拼接图像
            for idx, frame in enumerate(resized_frames):
                combined_image.paste(frame, (idx * target_width, 0))

            # 转换为base64
            buffer = io.BytesIO()
            combined_image.save(buffer, format='JPEG', quality=85)
            result_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            return result_base64
            
        except Exception as e:
            logger.error(f"GIF转换失败: {str(e)}")
            return None


# 创建全局单例
image_manager = ImageManager()


def image_path_to_base64(image_path: str) -> str:
    """将图片路径转换为base64编码
    Args:
        image_path: 图片文件路径
    Returns:
        str: base64编码的图片数据
    """
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
            return base64.b64encode(image_data).decode("utf-8")
    except Exception as e:
        logger.error(f"读取图片失败: {image_path}, 错误: {str(e)}")
        return None
