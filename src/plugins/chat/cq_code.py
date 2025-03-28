import base64
import html
import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional, Union
import ssl
import os
import aiohttp
from src.common.logger import get_module_logger
from nonebot import get_driver

from ..models.utils_model import LLM_request
from ..config.config import global_config
from .mapper import emojimapper
from .message_base import Seg
from .utils_user import get_user_nickname, get_groupname
from .message_base import GroupInfo, UserInfo

driver = get_driver()
config = driver.config

# 创建SSL上下文
ssl_context = ssl.create_default_context()
ssl_context.set_ciphers("AES128-GCM-SHA256")

logger = get_module_logger("cq_code")


@dataclass
class CQCode:
    """
    CQ码数据类，用于存储和处理CQ码

    属性:
        type: CQ码类型（如'image', 'at', 'face'等）
        params: CQ码的参数字典
        raw_code: 原始CQ码字符串
        translated_segments: 经过处理后的Seg对象列表
    """

    type: str
    params: Dict[str, str]
    group_info: Optional[GroupInfo] = None
    user_info: Optional[UserInfo] = None
    translated_segments: Optional[Union[Seg, List[Seg]]] = None
    reply_message: Dict = None  # 存储回复消息
    image_base64: Optional[str] = None
    _llm: Optional[LLM_request] = None

    def __post_init__(self):
        """初始化LLM实例"""
        pass

    async def translate(self):
        """根据CQ码类型进行相应的翻译处理，转换为Seg对象"""
        if self.type == "text":
            self.translated_segments = Seg(type="text", data=self.params.get("text", ""))
        elif self.type == "image":
            base64_data = await self.translate_image()
            if base64_data:
                if self.params.get("sub_type") == "0":
                    self.translated_segments = Seg(type="image", data=base64_data)
                else:
                    self.translated_segments = Seg(type="emoji", data=base64_data)
            else:
                self.translated_segments = Seg(type="text", data="[图片]")
        elif self.type == "at":
            if self.params.get("qq") == "all":
                self.translated_segments = Seg(type="text", data="@[全体成员]")
            else:
                user_nickname = get_user_nickname(self.params.get("qq", ""))
                self.translated_segments = Seg(type="text", data=f"[@{user_nickname or '某人'}]")
        elif self.type == "reply":
            reply_segments = await self.translate_reply()
            if reply_segments:
                self.translated_segments = Seg(type="seglist", data=reply_segments)
            else:
                self.translated_segments = Seg(type="text", data="[回复某人消息]")
        elif self.type == "face":
            face_id = self.params.get("id", "")
            self.translated_segments = Seg(type="text", data=f"[{emojimapper.get(int(face_id), '表情')}]")
        elif self.type == "forward":
            forward_segments = await self.translate_forward()
            if forward_segments:
                self.translated_segments = Seg(type="seglist", data=forward_segments)
            else:
                self.translated_segments = Seg(type="text", data="[转发消息]")
        else:
            self.translated_segments = Seg(type="text", data=f"[{self.type}]")

    async def get_img(self) -> Optional[str]:
        """异步获取图片并转换为base64"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/50.0.2661.87 Safari/537.36",
            "Accept": "text/html, application/xhtml xml, */*",
            "Accept-Encoding": "gbk, GB2312",
            "Accept-Language": "zh-cn",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        }

        url = html.unescape(self.params["url"])
        if not url.startswith(("http://", "https://")):
            return None

        max_retries = 3
        for retry in range(max_retries):
            try:
                logger.debug(f"获取图片中: {url}")
                # 设置SSL上下文和创建连接器
                conn = aiohttp.TCPConnector(ssl=ssl_context)
                async with aiohttp.ClientSession(connector=conn) as session:
                    async with session.get(
                        url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                        allow_redirects=True,
                    ) as response:
                        # 腾讯服务器特殊状态码处理
                        if response.status == 400 and "multimedia.nt.qq.com.cn" in url:
                            return None

                        if response.status != 200:
                            raise aiohttp.ClientError(f"HTTP {response.status}")

                        # 验证内容类型
                        content_type = response.headers.get("Content-Type", "")
                        if not content_type.startswith("image/"):
                            raise ValueError(f"非图片内容类型: {content_type}")

                        # 读取响应内容
                        content = await response.read()
                        logger.debug(f"获取图片成功: {url}")

                        # 转换为Base64
                        image_base64 = base64.b64encode(content).decode("utf-8")
                        self.image_base64 = image_base64
                        return image_base64

            except (aiohttp.ClientError, ValueError) as e:
                if retry == max_retries - 1:
                    logger.error(f"最终请求失败: {str(e)}")
                await asyncio.sleep(1.5**retry)  # 指数退避

            except Exception as e:
                logger.exception(f"获取图片时发生未知错误: {str(e)}")
                return None

        return None

    async def translate_image(self) -> Optional[str]:
        """处理图片类型的CQ码，返回base64字符串"""
        if "url" not in self.params:
            return None
        return await self.get_img()

    async def translate_forward(self) -> Optional[List[Seg]]:
        """处理转发消息，返回Seg列表"""
        try:
            if "content" not in self.params:
                return None

            content = self.unescape(self.params["content"])
            import ast

            try:
                messages = ast.literal_eval(content)
            except ValueError as e:
                logger.error(f"解析转发消息内容失败: {str(e)}")
                return None

            formatted_segments = []
            for msg in messages:
                sender = msg.get("sender", {})
                nickname = sender.get("card") or sender.get("nickname", "未知用户")
                raw_message = msg.get("raw_message", "")
                message_array = msg.get("message", [])

                if message_array and isinstance(message_array, list):
                    for message_part in message_array:
                        if message_part.get("type") == "forward":
                            content_seg = Seg(type="text", data="[转发消息]")
                            break
                        else:
                            if raw_message:
                                from .message_cq import MessageRecvCQ

                                user_info = UserInfo(
                                    platform="qq",
                                    user_id=msg.get("user_id", 0),
                                    user_nickname=nickname,
                                )
                                group_info = GroupInfo(
                                    platform="qq",
                                    group_id=msg.get("group_id", 0),
                                    group_name=get_groupname(msg.get("group_id", 0)),
                                )

                                message_obj = MessageRecvCQ(
                                    message_id=msg.get("message_id", 0),
                                    user_info=user_info,
                                    raw_message=raw_message,
                                    plain_text=raw_message,
                                    group_info=group_info,
                                )
                                await message_obj.initialize()
                                content_seg = Seg(type="seglist", data=[message_obj.message_segment])
                            else:
                                content_seg = Seg(type="text", data="[空消息]")
                else:
                    if raw_message:
                        from .message_cq import MessageRecvCQ

                        user_info = UserInfo(
                            platform="qq",
                            user_id=msg.get("user_id", 0),
                            user_nickname=nickname,
                        )
                        group_info = GroupInfo(
                            platform="qq",
                            group_id=msg.get("group_id", 0),
                            group_name=get_groupname(msg.get("group_id", 0)),
                        )
                        message_obj = MessageRecvCQ(
                            message_id=msg.get("message_id", 0),
                            user_info=user_info,
                            raw_message=raw_message,
                            plain_text=raw_message,
                            group_info=group_info,
                        )
                        await message_obj.initialize()
                        content_seg = Seg(type="seglist", data=[message_obj.message_segment])
                    else:
                        content_seg = Seg(type="text", data="[空消息]")

                formatted_segments.append(Seg(type="text", data=f"{nickname}: "))
                formatted_segments.append(content_seg)
                formatted_segments.append(Seg(type="text", data="\n"))

            return formatted_segments

        except Exception as e:
            logger.error(f"处理转发消息失败: {str(e)}")
            return None

    async def translate_reply(self) -> Optional[List[Seg]]:
        """处理回复类型的CQ码，返回Seg列表"""
        from .message_cq import MessageRecvCQ

        if self.reply_message is None:
            return None
        if hasattr(self.reply_message, "group_id"):
            group_info = GroupInfo(platform="qq", group_id=self.reply_message.group_id, group_name="")
        else:
            group_info = None

        if self.reply_message.sender.user_id:
            message_obj = MessageRecvCQ(
                user_info=UserInfo(
                    user_id=self.reply_message.sender.user_id, user_nickname=self.reply_message.sender.nickname
                ),
                message_id=self.reply_message.message_id,
                raw_message=str(self.reply_message.message),
                group_info=group_info,
            )
            await message_obj.initialize()

            segments = []
            if message_obj.message_info.user_info.user_id == global_config.BOT_QQ:
                segments.append(Seg(type="text", data=f"[回复 {global_config.BOT_NICKNAME} 的消息: "))
            else:
                segments.append(
                    Seg(
                        type="text",
                        data=f"[回复 {self.reply_message.sender.nickname} 的消息: ",
                    )
                )

            segments.append(Seg(type="seglist", data=[message_obj.message_segment]))
            segments.append(Seg(type="text", data="]"))
            return segments
        else:
            return None

    @staticmethod
    def unescape(text: str) -> str:
        """反转义CQ码中的特殊字符"""
        return text.replace("&#44;", ",").replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")


class CQCode_tool:
    @staticmethod
    def cq_from_dict_to_class(cq_code: Dict, msg, reply: Optional[Dict] = None) -> CQCode:
        """
        将CQ码字典转换为CQCode对象

        Args:
            cq_code: CQ码字典
            msg: MessageCQ对象
            reply: 回复消息的字典（可选）

        Returns:
            CQCode对象
        """
        # 处理字典形式的CQ码
        # 从cq_code字典中获取type字段的值,如果不存在则默认为'text'
        cq_type = cq_code.get("type", "text")
        params = {}
        if cq_type == "text":
            params["text"] = cq_code.get("data", {}).get("text", "")
        else:
            params = cq_code.get("data", {})

        instance = CQCode(
            type=cq_type,
            params=params,
            group_info=msg.message_info.group_info,
            user_info=msg.message_info.user_info,
            reply_message=reply,
        )

        return instance

    @staticmethod
    def create_reply_cq(message_id: int) -> str:
        """
        创建回复CQ码
        Args:
            message_id: 回复的消息ID
        Returns:
            回复CQ码字符串
        """
        return f"[CQ:reply,id={message_id}]"

    @staticmethod
    def create_emoji_cq(file_path: str) -> str:
        """
        创建表情包CQ码
        Args:
            file_path: 本地表情包文件路径
        Returns:
            表情包CQ码字符串
        """
        # 确保使用绝对路径
        abs_path = os.path.abspath(file_path)
        # 转义特殊字符
        escaped_path = abs_path.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;").replace(",", "&#44;")
        # 生成CQ码，设置sub_type=1表示这是表情包
        return f"[CQ:image,file=file:///{escaped_path},sub_type=1]"

    @staticmethod
    def create_emoji_cq_base64(base64_data: str) -> str:
        """
        创建表情包CQ码
        Args:
            base64_data: base64编码的表情包数据
        Returns:
            表情包CQ码字符串
        """
        # 转义base64数据
        escaped_base64 = (
            base64_data.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;").replace(",", "&#44;")
        )
        # 生成CQ码，设置sub_type=1表示这是表情包
        return f"[CQ:image,file=base64://{escaped_base64},sub_type=1]"

    @staticmethod
    def create_image_cq_base64(base64_data: str) -> str:
        """
        创建表情包CQ码
        Args:
            base64_data: base64编码的表情包数据
        Returns:
            表情包CQ码字符串
        """
        # 转义base64数据
        escaped_base64 = (
            base64_data.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;").replace(",", "&#44;")
        )
        # 生成CQ码，设置sub_type=1表示这是表情包
        return f"[CQ:image,file=base64://{escaped_base64},sub_type=0]"


cq_code_tool = CQCode_tool()
