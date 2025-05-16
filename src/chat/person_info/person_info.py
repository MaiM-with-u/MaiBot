from src.common.logger_manager import get_logger
from ...common.database import db
import copy
import hashlib
from typing import Any, Callable, Dict
import datetime
import asyncio
import numpy as np
from src.chat.models.utils_model import LLMRequest
from src.config.config import global_config
from src.individuality.individuality import Individuality

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
import pandas as pd
import json
import re


"""
PersonInfoManager 类方法功能摘要：
1. get_person_id - 根据平台和用户ID生成MD5哈希的唯一person_id
2. create_person_info - 创建新个人信息文档（自动合并默认值）
3. update_one_field - 更新单个字段值（若文档不存在则创建）
4. del_one_document - 删除指定person_id的文档
5. get_value - 获取单个字段值（返回实际值或默认值）
6. get_values - 批量获取字段值（任一字段无效则返回空字典）
7. del_all_undefined_field - 清理全集合中未定义的字段
8. get_specific_value_list - 根据指定条件，返回person_id,value字典
9. personal_habit_deduction - 定时推断个人习惯
"""


logger = get_logger("person_info")

person_info_default = {
    "person_id": None,
    "person_name": None,
    "name_reason": None,
    "platform": None,
    "user_id": None,
    "nickname": None,
    # "age" : 0,
    "relationship_value": 0,
    # "saved" : True,
    # "impression" : None,
    # "gender" : Unkown,
    "konw_time": 0,
    "msg_interval": 2000,
    "msg_interval_list": [],
    "user_cardname": None,  # 添加群名片
    "user_avatar": None,  # 添加头像信息（例如URL或标识符）
    "group_nicknames": [],
}  # 个人信息的各项与默认值在此定义，以下处理会自动创建/补全每一项


class PersonInfoManager:
    def __init__(self):
        self.person_name_list = {}
        self.qv_name_llm = LLMRequest(
            model=global_config.llm_normal,
            max_tokens=256,
            request_type="qv_name",
        )
        if "person_info" not in db.list_collection_names():
            db.create_collection("person_info")
            db.person_info.create_index("person_id", unique=True)

        # 初始化时读取所有person_name
        cursor = db.person_info.find({"person_name": {"$exists": True}}, {"person_id": 1, "person_name": 1, "_id": 0})
        for doc in cursor:
            if doc.get("person_name"):
                self.person_name_list[doc["person_id"]] = doc["person_name"]
        logger.debug(f"已加载 {len(self.person_name_list)} 个用户名称")

    @staticmethod
    def get_person_id(platform: str, user_id: int):
        """获取唯一id"""
        # 如果platform中存在-，就截取-后面的部分
        if "-" in platform:
            platform = platform.split("-")[1]

        components = [platform, str(user_id)]
        key = "_".join(components)
        return hashlib.md5(key.encode()).hexdigest()

    def is_person_known(self, platform: str, user_id: int):
        """判断是否认识某人"""
        person_id = self.get_person_id(platform, user_id)
        document = db.person_info.find_one({"person_id": person_id})
        if document:
            return True
        else:
            return False

    @staticmethod
    async def create_person_info(person_id: str, data: dict = None):
        """创建一个项"""
        if not person_id:
            logger.debug("创建失败，personid不存在")
            return

        _person_info_default = copy.deepcopy(person_info_default)
        _person_info_default["person_id"] = person_id

        if data:
            for key in _person_info_default:
                if key != "person_id" and key in data:
                    _person_info_default[key] = data[key]

        db.person_info.insert_one(_person_info_default)

    async def update_one_field(self, person_id: str, field_name: str, value, data: dict = None):
        """更新某一个字段，会补全"""
        if field_name not in person_info_default.keys():
            logger.debug(f"更新'{field_name}'失败，未定义的字段")
            return

        document = db.person_info.find_one({"person_id": person_id})

        if document:
            db.person_info.update_one({"person_id": person_id}, {"$set": {field_name: value}})
        else:
            data[field_name] = value
            logger.debug(f"更新时{person_id}不存在，已新建")
            await self.create_person_info(person_id, data)

    @staticmethod
    async def has_one_field(person_id: str, field_name: str):
        """判断是否存在某一个字段"""
        document = db.person_info.find_one({"person_id": person_id}, {field_name: 1})
        if document:
            return True
        else:
            return False

    @staticmethod
    def _extract_json_from_text(text: str) -> dict:
        """从文本中提取JSON数据的高容错方法"""
        try:
            # 尝试直接解析
            parsed_json = json.loads(text)
            # 如果解析结果是列表，尝试取第一个元素
            if isinstance(parsed_json, list):
                if parsed_json:  # 检查列表是否为空
                    parsed_json = parsed_json[0]
                else:  # 如果列表为空，重置为 None，走后续逻辑
                    parsed_json = None
            # 确保解析结果是字典
            if isinstance(parsed_json, dict):
                return parsed_json

        except json.JSONDecodeError:
            # 解析失败，继续尝试其他方法
            pass
        except Exception as e:
            logger.warning(f"尝试直接解析JSON时发生意外错误: {e}")
            pass  # 继续尝试其他方法

        # 如果直接解析失败或结果不是字典
        try:
            # 尝试找到JSON对象格式的部分
            json_pattern = r"\{[^{}]*\}"
            matches = re.findall(json_pattern, text)
            if matches:
                parsed_obj = json.loads(matches[0])
                if isinstance(parsed_obj, dict):  # 确保是字典
                    return parsed_obj

            # 如果上面都失败了，尝试提取键值对
            nickname_pattern = r'"nickname"[:\s]+"([^"]+)"'
            reason_pattern = r'"reason"[:\s]+"([^"]+)"'

            nickname_match = re.search(nickname_pattern, text)
            reason_match = re.search(reason_pattern, text)

            if nickname_match:
                return {
                    "nickname": nickname_match.group(1),
                    "reason": reason_match.group(1) if reason_match else "未提供理由",
                }
        except Exception as e:
            logger.error(f"后备JSON提取失败: {str(e)}")

        # 如果所有方法都失败了，返回默认字典
        logger.warning(f"无法从文本中提取有效的JSON字典: {text}")
        return {"nickname": "", "reason": ""}

    async def qv_person_name(
        self, person_id: str, user_nickname: str, user_cardname: str, user_avatar: str, request: str = ""
    ):
        """给某个用户取名"""
        if not person_id:
            logger.debug("取名失败：person_id不能为空")
            return None

        old_name = await self.get_value(person_id, "person_name")
        old_reason = await self.get_value(person_id, "name_reason")

        max_retries = 5  # 最大重试次数
        current_try = 0
        existing_names = ""
        while current_try < max_retries:
            individuality = Individuality.get_instance()
            prompt_personality = individuality.get_prompt(x_person=2, level=1)
            bot_name = individuality.personality.bot_nickname

            qv_name_prompt = f"你是{bot_name}，{prompt_personality}"
            qv_name_prompt += f"现在你想给一个用户取一个昵称，用户是的qq昵称是{user_nickname}，"
            qv_name_prompt += f"用户的qq群昵称名是{user_cardname}，"
            if user_avatar:
                qv_name_prompt += f"用户的qq头像是{user_avatar}，"
            if old_name:
                qv_name_prompt += f"你之前叫他{old_name}，是因为{old_reason}，"

            qv_name_prompt += f"\n其他取名的要求是：{request}，不要太浮夸"

            qv_name_prompt += (
                "\n请根据以上用户信息，想想你叫他什么比较好，不要太浮夸，请最好使用用户的qq昵称，可以稍作修改"
            )
            if existing_names:
                qv_name_prompt += f"\n请注意，以下名称已被使用，不要使用以下昵称：{existing_names}。\n"
            qv_name_prompt += "请用json给出你的想法，并给出理由，示例如下："
            qv_name_prompt += """{
                "nickname": "昵称",
                "reason": "理由"
            }"""
            # logger.debug(f"取名提示词：{qv_name_prompt}")
            response = await self.qv_name_llm.generate_response(qv_name_prompt)
            logger.trace(f"取名提示词：{qv_name_prompt}\n取名回复：{response}")
            result = self._extract_json_from_text(response[0])

            if not result["nickname"]:
                logger.error("生成的昵称为空，重试中...")
                current_try += 1
                continue

            # 检查生成的昵称是否已存在
            if result["nickname"] not in self.person_name_list.values():
                # 更新数据库和内存中的列表
                await self.update_one_field(person_id, "person_name", result["nickname"])
                # await self.update_one_field(person_id, "nickname", user_nickname)
                # await self.update_one_field(person_id, "avatar", user_avatar)
                await self.update_one_field(person_id, "name_reason", result["reason"])

                self.person_name_list[person_id] = result["nickname"]
                # logger.debug(f"用户 {person_id} 的名称已更新为 {result['nickname']}，原因：{result['reason']}")
                return result
            else:
                existing_names += f"{result['nickname']}、"

            logger.debug(f"生成的昵称 {result['nickname']} 已存在，重试中...")
            current_try += 1

        logger.error(f"在{max_retries}次尝试后仍未能生成唯一昵称")
        return None

    @staticmethod
    async def del_one_document(person_id: str):
        """删除指定 person_id 的文档"""
        if not person_id:
            logger.debug("删除失败：person_id 不能为空")
            return

        result = db.person_info.delete_one({"person_id": person_id})
        if result.deleted_count > 0:
            logger.debug(f"删除成功：person_id={person_id}")
        else:
            logger.debug(f"删除失败：未找到 person_id={person_id}")

    @staticmethod
    async def get_value(person_id: str, field_name: str):
        """获取指定person_id文档的字段值，若不存在该字段，则返回该字段的全局默认值"""
        if not person_id:
            logger.debug("get_value获取失败：person_id不能为空")
            return None

        if field_name not in person_info_default:
            logger.debug(f"get_value获取失败：字段'{field_name}'未定义")
            return None

        document = db.person_info.find_one({"person_id": person_id}, {field_name: 1})

        if document and field_name in document:
            return document[field_name]
        else:
            default_value = copy.deepcopy(person_info_default[field_name])
            logger.trace(f"获取{person_id}的{field_name}失败，已返回默认值{default_value}")
            return default_value

    @staticmethod
    async def get_values(person_id: str, field_names: list) -> dict:
        """获取指定person_id文档的多个字段值，若不存在该字段，则返回该字段的全局默认值"""
        if not person_id:
            logger.debug("get_values获取失败：person_id不能为空")
            return {}

        # 检查所有字段是否有效
        for field in field_names:
            if field not in person_info_default:
                logger.debug(f"get_values获取失败：字段'{field}'未定义")
                return {}

        # 构建查询投影（所有字段都有效才会执行到这里）
        projection = {field: 1 for field in field_names}

        document = db.person_info.find_one({"person_id": person_id}, projection)

        result = {}
        for field in field_names:
            result[field] = copy.deepcopy(
                document.get(field, person_info_default[field]) if document else person_info_default[field]
            )

        return result

    @staticmethod
    async def del_all_undefined_field():
        """删除所有项里的未定义字段"""
        # 获取所有已定义的字段名
        defined_fields = set(person_info_default.keys())

        try:
            # 遍历集合中的所有文档
            for document in db.person_info.find({}):
                # 找出文档中未定义的字段
                undefined_fields = set(document.keys()) - defined_fields - {"_id"}

                if undefined_fields:
                    # 构建更新操作，使用$unset删除未定义字段
                    update_result = db.person_info.update_one(
                        {"_id": document["_id"]}, {"$unset": {field: 1 for field in undefined_fields}}
                    )

                    if update_result.modified_count > 0:
                        logger.debug(f"已清理文档 {document['_id']} 的未定义字段: {undefined_fields}")

            return

        except Exception as e:
            logger.error(f"清理未定义字段时出错: {e}")
            return

    @staticmethod
    async def get_specific_value_list(
        field_name: str,
        way: Callable[[Any], bool],  # 接受任意类型值
    ) -> Dict[str, Any]:
        """
        获取满足条件的字段值字典

        Args:
            field_name: 目标字段名
            way: 判断函数 (value: Any) -> bool

        Returns:
            {person_id: value} | {}

        Example:
            # 查找所有nickname包含"admin"的用户
            result = manager.specific_value_list(
                "nickname",
                lambda x: "admin" in x.lower()
            )
        """
        if field_name not in person_info_default:
            logger.error(f"字段检查失败：'{field_name}'未定义")
            return {}

        try:
            result = {}
            for doc in db.person_info.find({field_name: {"$exists": True}}, {"person_id": 1, field_name: 1, "_id": 0}):
                try:
                    value = doc[field_name]
                    if way(value):
                        result[doc["person_id"]] = value
                except (KeyError, TypeError, ValueError) as e:
                    logger.debug(f"记录{doc.get('person_id')}处理失败: {str(e)}")
                    continue

            return result

        except Exception as e:
            logger.error(f"数据库查询失败: {str(e)}", exc_info=True)
            return {}

    async def personal_habit_deduction(self):
        """启动个人信息推断，每天根据一定条件推断一次"""
        try:
            while 1:
                await asyncio.sleep(600)
                current_time = datetime.datetime.now()
                logger.info(f"个人信息推断启动: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")

                # "msg_interval"推断
                msg_interval_map = False
                msg_interval_lists = await self.get_specific_value_list(
                    "msg_interval_list", lambda x: isinstance(x, list) and len(x) >= 100
                )
                for person_id, msg_interval_list_ in msg_interval_lists.items():
                    await asyncio.sleep(0.3)
                    try:
                        time_interval = []
                        for t1, t2 in zip(msg_interval_list_, msg_interval_list_[1:]):
                            delta = t2 - t1
                            if delta > 0:
                                time_interval.append(delta)

                        time_interval = [t for t in time_interval if 200 <= t <= 8000]
                        # --- 修改后的逻辑 ---
                        # 数据量检查 (至少需要 30 条有效间隔，并且足够进行头尾截断)
                        if len(time_interval) >= 30 + 10:  # 至少30条有效+头尾各5条
                            time_interval.sort()

                            # 画图(log) - 这部分保留
                            msg_interval_map = True
                            log_dir = Path("logs/person_info")
                            log_dir.mkdir(parents=True, exist_ok=True)
                            plt.figure(figsize=(10, 6))
                            # 使用截断前的数据画图，更能反映原始分布
                            time_series_original = pd.Series(time_interval)
                            plt.hist(
                                time_series_original,
                                bins=50,
                                density=True,
                                alpha=0.4,
                                color="pink",
                                label="Histogram (Original Filtered)",
                            )
                            time_series_original.plot(
                                kind="kde", color="mediumpurple", linewidth=1, label="Density (Original Filtered)"
                            )
                            plt.grid(True, alpha=0.2)
                            plt.xlim(0, 8000)
                            plt.title(f"Message Interval Distribution (User: {person_id[:8]}...)")
                            plt.xlabel("Interval (ms)")
                            plt.ylabel("Density")
                            plt.legend(framealpha=0.9, facecolor="white")
                            img_path = log_dir / f"interval_distribution_{person_id[:8]}.png"
                            plt.savefig(img_path)
                            plt.close()
                            # 画图结束

                            # 去掉头尾各 5 个数据点
                            trimmed_interval = time_interval[5:-5]

                            # 计算截断后数据的 37% 分位数
                            if trimmed_interval:  # 确保截断后列表不为空
                                msg_interval = int(round(np.percentile(trimmed_interval, 37)))
                                # 更新数据库
                                await self.update_one_field(person_id, "msg_interval", msg_interval)
                                logger.trace(f"用户{person_id}的msg_interval通过头尾截断和37分位数更新为{msg_interval}")
                            else:
                                logger.trace(f"用户{person_id}截断后数据为空，无法计算msg_interval")
                        else:
                            logger.trace(
                                f"用户{person_id}有效消息间隔数量 ({len(time_interval)}) 不足进行推断 (需要至少 {30 + 10} 条)"
                            )
                        # --- 修改结束 ---
                    except Exception as e:
                        logger.trace(f"用户{person_id}消息间隔计算失败: {type(e).__name__}: {str(e)}")
                        continue

                # 其他...

                if msg_interval_map:
                    logger.trace("已保存分布图到: logs/person_info")
                current_time = datetime.datetime.now()
                logger.trace(f"个人信息推断结束: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
                await asyncio.sleep(86400)

        except Exception as e:
            logger.error(f"个人信息推断运行时出错: {str(e)}")
            logger.exception("详细错误信息：")

    async def get_or_create_person(
        self, platform: str, user_id: int, nickname: str = None, user_cardname: str = None, user_avatar: str = None
    ) -> str:
        """
        根据 platform 和 user_id 获取 person_id。
        如果对应的用户不存在，则使用提供的可选信息创建新用户。

        Args:
            platform: 平台标识
            user_id: 用户在该平台上的ID
            nickname: 用户的昵称 (可选，用于创建新用户)
            user_cardname: 用户的群名片 (可选，用于创建新用户)
            user_avatar: 用户的头像信息 (可选，用于创建新用户)

        Returns:
            对应的 person_id。
        """
        person_id = self.get_person_id(platform, user_id)

        # 检查用户是否已存在
        # 使用静态方法 get_person_id，因此可以直接调用 db
        document = db.person_info.find_one({"person_id": person_id})

        if document is None:
            logger.info(f"用户 {platform}:{user_id} (person_id: {person_id}) 不存在，将创建新记录。")
            initial_data = {
                "platform": platform,
                "user_id": user_id,
                "nickname": nickname,
                "konw_time": int(datetime.datetime.now().timestamp()),  # 添加初次认识时间
                # 注意：这里没有添加 user_cardname 和 user_avatar，因为它们不在 person_info_default 中
                # 如果需要存储它们，需要先在 person_info_default 中定义
            }
            # 过滤掉值为 None 的初始数据
            initial_data = {k: v for k, v in initial_data.items() if v is not None}

            # 注意：create_person_info 是静态方法
            await PersonInfoManager.create_person_info(person_id, data=initial_data)
            # 创建后，可以考虑立即为其取名，但这可能会增加延迟
            # await self.qv_person_name(person_id, nickname, user_cardname, user_avatar)
            logger.debug(f"已为 {person_id} 创建新记录，初始数据: {initial_data}")

        return person_id

    async def get_person_info_by_name(self, person_name: str) -> dict | None:
        """根据 person_name 查找用户并返回基本信息 (如果找到)"""
        if not person_name:
            logger.debug("get_person_info_by_name 获取失败：person_name 不能为空")
            return None

        # 优先从内存缓存查找 person_id
        found_person_id = None
        for pid, name in self.person_name_list.items():
            if name == person_name:
                found_person_id = pid
                break  # 找到第一个匹配就停止

        if not found_person_id:
            # 如果内存没有，尝试数据库查询（可能内存未及时更新或启动时未加载）
            document = db.person_info.find_one({"person_name": person_name})
            if document:
                found_person_id = document.get("person_id")
            else:
                logger.debug(f"数据库中也未找到名为 '{person_name}' 的用户")
                return None  # 数据库也找不到

        # 根据找到的 person_id 获取所需信息
        if found_person_id:
            required_fields = ["person_id", "platform", "user_id", "nickname", "user_cardname", "user_avatar"]
            person_data = await self.get_values(found_person_id, required_fields)
            if person_data:  # 确保 get_values 成功返回
                return person_data
            else:
                logger.warning(f"找到了 person_id '{found_person_id}' 但获取详细信息失败")
                return None
        else:
            # 这理论上不应该发生，因为上面已经处理了找不到的情况
            logger.error(f"逻辑错误：未能为 '{person_name}' 确定 person_id")
            return None


person_info_manager = PersonInfoManager()
