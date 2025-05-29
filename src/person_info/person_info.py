from src.common.logger_manager import get_logger
from src.common.database.database import db
from src.common.database.database_model import PersonInfo  # 新增导入
import copy
import hashlib
from typing import Any, Callable, Dict
import datetime
import asyncio
import numpy as np
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config
from src.individuality.individuality import individuality

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
import pandas as pd
import json  # 新增导入
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
    "person_name": None,  # 模型中已设为 null=True，此默认值OK
    "name_reason": None,
    "platform": "unknown",  # 提供非None的默认值
    "user_id": "unknown",  # 提供非None的默认值
    "nickname": "Unknown",  # 提供非None的默认值
    "relationship_value": 0,
    "know_time": 0,  # 修正拼写：konw_time -> know_time
    "msg_interval": 2000,
    "msg_interval_list": [],  # 将作为 JSON 字符串存储在 Peewee 的 TextField
    "user_cardname": None,  # 注意：此字段不在 PersonInfo Peewee 模型中
    "user_avatar": None,  # 注意：此字段不在 PersonInfo Peewee 模型中
}


class PersonInfoManager:
    def __init__(self):
        self.person_name_list = {}
        # TODO: API-Adapter修改标记
        self.qv_name_llm = LLMRequest(
            model=global_config.model.utils,
            max_tokens=256,
            request_type="relation.qv_name",
        )
        try:
            db.connect(reuse_if_open=True)
            db.create_tables([PersonInfo], safe=True)
        except Exception as e:
            logger.error(f"数据库连接或 PersonInfo 表创建失败: {e}")

        # 初始化时读取所有person_name
        try:
            for record in PersonInfo.select(PersonInfo.person_id, PersonInfo.person_name).where(
                PersonInfo.person_name.is_null(False)
            ):
                if record.person_name:
                    self.person_name_list[record.person_id] = record.person_name
            logger.debug(f"已加载 {len(self.person_name_list)} 个用户名称 (Peewee)")
        except Exception as e:
            logger.error(f"从 Peewee 加载 person_name_list 失败: {e}")

    @staticmethod
    def get_person_id(platform: str, user_id: int):
        """获取唯一id"""
        if "-" in platform:
            platform = platform.split("-")[1]

        components = [platform, str(user_id)]
        key = "_".join(components)
        return hashlib.md5(key.encode()).hexdigest()

    async def is_person_known(self, platform: str, user_id: int):
        """判断是否认识某人"""
        person_id = self.get_person_id(platform, user_id)

        def _db_check_known_sync(p_id: str):
            return PersonInfo.get_or_none(PersonInfo.person_id == p_id) is not None

        try:
            return await asyncio.to_thread(_db_check_known_sync, person_id)
        except Exception as e:
            logger.error(f"检查用户 {person_id} 是否已知时出错 (Peewee): {e}")
            return False

    def get_person_id_by_person_name(self, person_name: str):
        """根据用户名获取用户ID"""
        try:
            record = PersonInfo.get_or_none(PersonInfo.person_name == person_name)
            if record:
                return record.person_id
            else:
                return ""
        except Exception as e:
            logger.error(f"根据用户名 {person_name} 获取用户ID时出错 (Peewee): {e}")
            return ""

    @staticmethod
    async def create_person_info(person_id: str, data: dict = None):
        """创建一个项"""
        if not person_id:
            logger.debug("创建失败，personid不存在")
            return

        _person_info_default = copy.deepcopy(person_info_default)
        model_fields = PersonInfo._meta.fields.keys()

        final_data = {"person_id": person_id}

        if data:
            for key, value in data.items():
                if key in model_fields:
                    final_data[key] = value

        for key, default_value in _person_info_default.items():
            if key in model_fields and key not in final_data:
                final_data[key] = default_value

        if "msg_interval_list" in final_data and isinstance(final_data["msg_interval_list"], list):
            final_data["msg_interval_list"] = json.dumps(final_data["msg_interval_list"])
        elif "msg_interval_list" not in final_data and "msg_interval_list" in model_fields:
            final_data["msg_interval_list"] = json.dumps([])

        def _db_create_sync(p_data: dict):
            try:
                PersonInfo.create(**p_data)
                return True
            except Exception as e:
                logger.error(f"创建 PersonInfo 记录 {p_data.get('person_id')} 失败 (Peewee): {e}")
                return False

        await asyncio.to_thread(_db_create_sync, final_data)

    async def update_one_field(self, person_id: str, field_name: str, value, data: dict = None):
        """更新某一个字段，会补全"""
        if field_name not in PersonInfo._meta.fields:
            if field_name in person_info_default:
                logger.debug(f"更新'{field_name}'跳过，字段存在于默认配置但不在 PersonInfo Peewee 模型中。")
                return
            logger.debug(f"更新'{field_name}'失败，未在 PersonInfo Peewee 模型中定义的字段。")
            return

        def _db_update_sync(p_id: str, f_name: str, val):
            record = PersonInfo.get_or_none(PersonInfo.person_id == p_id)
            if record:
                if f_name == "msg_interval_list" and isinstance(val, list):
                    setattr(record, f_name, json.dumps(val))
                else:
                    setattr(record, f_name, val)
                record.save()
                return True, False
            return False, True

        found, needs_creation = await asyncio.to_thread(_db_update_sync, person_id, field_name, value)

        if needs_creation:
            logger.debug(f"更新时 {person_id} 不存在，将新建。")
            creation_data = data if data is not None else {}
            creation_data[field_name] = value
            if "platform" not in creation_data or "user_id" not in creation_data:
                logger.warning(f"为 {person_id} 创建记录时，platform/user_id 可能缺失。")

            await self.create_person_info(person_id, creation_data)

    @staticmethod
    async def has_one_field(person_id: str, field_name: str):
        """判断是否存在某一个字段"""
        if field_name not in PersonInfo._meta.fields:
            logger.debug(f"检查字段'{field_name}'失败，未在 PersonInfo Peewee 模型中定义。")
            return False

        def _db_has_field_sync(p_id: str, f_name: str):
            record = PersonInfo.get_or_none(PersonInfo.person_id == p_id)
            if record:
                return True
            return False

        try:
            return await asyncio.to_thread(_db_has_field_sync, person_id, field_name)
        except Exception as e:
            logger.error(f"检查字段 {field_name} for {person_id} 时出错 (Peewee): {e}")
            return False

    @staticmethod
    def _extract_json_from_text(text: str) -> dict:
        """从文本中提取JSON数据的高容错方法"""
        try:
            parsed_json = json.loads(text)
            if isinstance(parsed_json, list):
                if parsed_json:
                    parsed_json = parsed_json[0]
                else:
                    parsed_json = None
            if isinstance(parsed_json, dict):
                return parsed_json

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.warning(f"尝试直接解析JSON时发生意外错误: {e}")
            pass

        try:
            json_pattern = r"\{[^{}]*\}"
            matches = re.findall(json_pattern, text)
            if matches:
                parsed_obj = json.loads(matches[0])
                if isinstance(parsed_obj, dict):
                    return parsed_obj

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

        max_retries = 5
        current_try = 0
        existing_names_str = ""
        current_name_set = set(self.person_name_list.values())

        while current_try < max_retries:
            # prompt_personality = individuality.get_prompt(x_person=2, level=1)
            bot_name = individuality.personality.bot_nickname

            qv_name_prompt = f"你是{bot_name}，一个伪装成人类的AI，你不能让别人发现这一点，"
            qv_name_prompt += f"现在你想给一个用户取一个昵称，用户的qq昵称是{user_nickname}，"
            qv_name_prompt += f"用户的qq群昵称名是{user_cardname}，"
            if user_avatar:
                qv_name_prompt += f"用户的qq头像是{user_avatar}，"
            if old_name:
                qv_name_prompt += f"你之前叫他{old_name}，是因为{old_reason}，"

            qv_name_prompt += f"\n其他取名的要求是：{request}，不要太浮夸，简短，"
            qv_name_prompt += "\n请根据以上用户信息，想想你叫他什么比较好，不要太浮夸，请最好使用用户的qq昵称，可以稍作修改，优先使用原文。优先使用用户的qq昵称或者群昵称原文。"

            if existing_names_str:
                qv_name_prompt += f"\n请注意，以下名称已被你尝试过或已知存在，请避免：{existing_names_str}。\n"

            if len(current_name_set) < 50 and current_name_set:
                qv_name_prompt += f"已知的其他昵称有: {', '.join(list(current_name_set)[:10])}等。\n"

            qv_name_prompt += "请用json给出你的想法，并给出理由，示例如下："
            qv_name_prompt += """{
                "nickname": "昵称",
                "reason": "理由"
            }"""
            response = await self.qv_name_llm.generate_response(qv_name_prompt)
            logger.trace(f"取名提示词：{qv_name_prompt}\n取名回复：{response}")
            result = self._extract_json_from_text(response[0])

            if not result or not result.get("nickname"):
                logger.error("生成的昵称为空或结果格式不正确，重试中...")
                current_try += 1
                continue

            generated_nickname = result["nickname"]

            is_duplicate = False
            if generated_nickname in current_name_set:
                is_duplicate = True
            else:

                def _db_check_name_exists_sync(name_to_check):
                    return PersonInfo.select().where(PersonInfo.person_name == name_to_check).exists()

                if await asyncio.to_thread(_db_check_name_exists_sync, generated_nickname):
                    is_duplicate = True
                    current_name_set.add(generated_nickname)

            if not is_duplicate:
                await self.update_one_field(person_id, "person_name", generated_nickname)
                await self.update_one_field(person_id, "name_reason", result.get("reason", "未提供理由"))

                self.person_name_list[person_id] = generated_nickname
                return result
            else:
                if existing_names_str:
                    existing_names_str += "、"
                existing_names_str += generated_nickname
                logger.debug(f"生成的昵称 {generated_nickname} 已存在，重试中...")
                current_try += 1

        logger.error(f"在{max_retries}次尝试后仍未能生成唯一昵称 for {person_id}")
        return None

    @staticmethod
    async def del_one_document(person_id: str):
        """删除指定 person_id 的文档"""
        if not person_id:
            logger.debug("删除失败：person_id 不能为空")
            return

        def _db_delete_sync(p_id: str):
            try:
                query = PersonInfo.delete().where(PersonInfo.person_id == p_id)
                deleted_count = query.execute()
                return deleted_count
            except Exception as e:
                logger.error(f"删除 PersonInfo {p_id} 失败 (Peewee): {e}")
                return 0

        deleted_count = await asyncio.to_thread(_db_delete_sync, person_id)

        if deleted_count > 0:
            logger.debug(f"删除成功：person_id={person_id} (Peewee)")
        else:
            logger.debug(f"删除失败：未找到 person_id={person_id} 或删除未影响行 (Peewee)")

    @staticmethod
    async def get_value(person_id: str, field_name: str):
        """获取指定person_id文档的字段值，若不存在该字段，则返回该字段的全局默认值"""
        if not person_id:
            logger.debug("get_value获取失败：person_id不能为空")
            return person_info_default.get(field_name)

        if field_name not in PersonInfo._meta.fields:
            if field_name in person_info_default:
                logger.trace(f"字段'{field_name}'不在Peewee模型中，但存在于默认配置中。返回配置默认值。")
                return copy.deepcopy(person_info_default[field_name])
            logger.debug(f"get_value获取失败：字段'{field_name}'未在Peewee模型和默认配置中定义。")
            return None

        def _db_get_value_sync(p_id: str, f_name: str):
            record = PersonInfo.get_or_none(PersonInfo.person_id == p_id)
            if record:
                val = getattr(record, f_name)
                if f_name == "msg_interval_list" and isinstance(val, str):
                    try:
                        return json.loads(val)
                    except json.JSONDecodeError:
                        logger.warning(f"无法解析 {p_id} 的 msg_interval_list JSON: {val}")
                        return copy.deepcopy(person_info_default.get(f_name, []))
                return val
            return None

        value = await asyncio.to_thread(_db_get_value_sync, person_id, field_name)

        if value is not None:
            return value
        else:
            default_value = copy.deepcopy(person_info_default.get(field_name))
            logger.trace(f"获取{person_id}的{field_name}失败或值为None，已返回默认值{default_value} (Peewee)")
            return default_value

    @staticmethod
    async def get_values(person_id: str, field_names: list) -> dict:
        """获取指定person_id文档的多个字段值，若不存在该字段，则返回该字段的全局默认值"""
        if not person_id:
            logger.debug("get_values获取失败：person_id不能为空")
            return {}

        result = {}

        def _db_get_record_sync(p_id: str):
            return PersonInfo.get_or_none(PersonInfo.person_id == p_id)

        record = await asyncio.to_thread(_db_get_record_sync, person_id)

        for field_name in field_names:
            if field_name not in PersonInfo._meta.fields:
                if field_name in person_info_default:
                    result[field_name] = copy.deepcopy(person_info_default[field_name])
                    logger.trace(f"字段'{field_name}'不在Peewee模型中，使用默认配置值。")
                else:
                    logger.debug(f"get_values查询失败：字段'{field_name}'未在Peewee模型和默认配置中定义。")
                    result[field_name] = None
                continue

            if record:
                value = getattr(record, field_name)
                if field_name == "msg_interval_list" and isinstance(value, str):
                    try:
                        result[field_name] = json.loads(value)
                    except json.JSONDecodeError:
                        logger.warning(f"无法解析 {person_id} 的 msg_interval_list JSON: {value}")
                        result[field_name] = copy.deepcopy(person_info_default.get(field_name, []))
                elif value is not None:
                    result[field_name] = value
                else:
                    result[field_name] = copy.deepcopy(person_info_default.get(field_name))
            else:
                result[field_name] = copy.deepcopy(person_info_default.get(field_name))

        return result

    # @staticmethod
    # async def del_all_undefined_field():
    #     """删除所有项里的未定义字段 - 对于Peewee (SQL)，此操作通常不适用，因为模式是固定的。"""
    #     logger.info(
    #         "del_all_undefined_field: 对于使用Peewee的SQL数据库，此操作通常不适用或不需要，因为表结构是预定义的。"
    #     )
    #     return

    @staticmethod
    async def get_specific_value_list(
        field_name: str,
        way: Callable[[Any], bool],
    ) -> Dict[str, Any]:
        """
        获取满足条件的字段值字典
        """
        if field_name not in PersonInfo._meta.fields:
            logger.error(f"字段检查失败：'{field_name}'未在 PersonInfo Peewee 模型中定义")
            return {}

        def _db_get_specific_sync(f_name: str):
            found_results = {}
            try:
                for record in PersonInfo.select(PersonInfo.person_id, getattr(PersonInfo, f_name)):
                    value = getattr(record, f_name)
                    if f_name == "msg_interval_list" and isinstance(value, str):
                        try:
                            processed_value = json.loads(value)
                        except json.JSONDecodeError:
                            logger.warning(f"跳过记录 {record.person_id}，无法解析 msg_interval_list: {value}")
                            continue
                    else:
                        processed_value = value

                    if way(processed_value):
                        found_results[record.person_id] = processed_value
            except Exception as e_query:
                logger.error(f"数据库查询失败 (Peewee specific_value_list for {f_name}): {str(e_query)}", exc_info=True)
            return found_results

        try:
            return await asyncio.to_thread(_db_get_specific_sync, field_name)
        except Exception as e:
            logger.error(f"执行 get_specific_value_list 线程时出错: {str(e)}", exc_info=True)
            return {}

    async def personal_habit_deduction(self):
        """启动个人信息推断，每天根据一定条件推断一次"""
        try:
            while 1:
                await asyncio.sleep(600)
                current_time_dt = datetime.datetime.now()
                logger.info(f"个人信息推断启动: {current_time_dt.strftime('%Y-%m-%d %H:%M:%S')}")

                msg_interval_map_generated = False
                msg_interval_lists_map = await self.get_specific_value_list(
                    "msg_interval_list", lambda x: isinstance(x, list) and len(x) >= 100
                )

                for person_id, actual_msg_interval_list in msg_interval_lists_map.items():
                    await asyncio.sleep(0.3)
                    try:
                        time_interval = []
                        for t1, t2 in zip(actual_msg_interval_list, actual_msg_interval_list[1:]):
                            delta = t2 - t1
                            if delta > 0:
                                time_interval.append(delta)

                        time_interval = [t for t in time_interval if 200 <= t <= 8000]

                        if len(time_interval) >= 30 + 10:
                            time_interval.sort()
                            msg_interval_map_generated = True
                            log_dir = Path("logs/person_info")
                            log_dir.mkdir(parents=True, exist_ok=True)
                            plt.figure(figsize=(10, 6))
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

                            trimmed_interval = time_interval[5:-5]
                            if trimmed_interval:
                                msg_interval_val = int(round(np.percentile(trimmed_interval, 37)))
                                await self.update_one_field(person_id, "msg_interval", msg_interval_val)
                                logger.trace(
                                    f"用户{person_id}的msg_interval通过头尾截断和37分位数更新为{msg_interval_val}"
                                )
                            else:
                                logger.trace(f"用户{person_id}截断后数据为空，无法计算msg_interval")
                        else:
                            logger.trace(
                                f"用户{person_id}有效消息间隔数量 ({len(time_interval)}) 不足进行推断 (需要至少 {30 + 10} 条)"
                            )
                    except Exception as e_inner:
                        logger.trace(f"用户{person_id}消息间隔计算失败: {type(e_inner).__name__}: {str(e_inner)}")
                        continue

                if msg_interval_map_generated:
                    logger.trace("已保存分布图到: logs/person_info")

                current_time_dt_end = datetime.datetime.now()
                logger.trace(f"个人信息推断结束: {current_time_dt_end.strftime('%Y-%m-%d %H:%M:%S')}")
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
        """
        person_id = self.get_person_id(platform, user_id)

        def _db_check_exists_sync(p_id: str):
            return PersonInfo.get_or_none(PersonInfo.person_id == p_id)

        record = await asyncio.to_thread(_db_check_exists_sync, person_id)

        if record is None:
            logger.info(f"用户 {platform}:{user_id} (person_id: {person_id}) 不存在，将创建新记录 (Peewee)。")
            initial_data = {
                "platform": platform,
                "user_id": str(user_id),
                "nickname": nickname,
                "know_time": int(datetime.datetime.now().timestamp()),  # 修正拼写：konw_time -> know_time
            }
            model_fields = PersonInfo._meta.fields.keys()
            filtered_initial_data = {k: v for k, v in initial_data.items() if v is not None and k in model_fields}

            await self.create_person_info(person_id, data=filtered_initial_data)
            logger.debug(f"已为 {person_id} 创建新记录，初始数据 (filtered for model): {filtered_initial_data}")

        return person_id

    async def get_person_info_by_name(self, person_name: str) -> dict | None:
        """根据 person_name 查找用户并返回基本信息 (如果找到)"""
        if not person_name:
            logger.debug("get_person_info_by_name 获取失败：person_name 不能为空")
            return None

        found_person_id = None
        for pid, name_in_cache in self.person_name_list.items():
            if name_in_cache == person_name:
                found_person_id = pid
                break

        if not found_person_id:

            def _db_find_by_name_sync(p_name_to_find: str):
                return PersonInfo.get_or_none(PersonInfo.person_name == p_name_to_find)

            record = await asyncio.to_thread(_db_find_by_name_sync, person_name)
            if record:
                found_person_id = record.person_id
                if (
                    found_person_id not in self.person_name_list
                    or self.person_name_list[found_person_id] != person_name
                ):
                    self.person_name_list[found_person_id] = person_name
            else:
                logger.debug(f"数据库中也未找到名为 '{person_name}' 的用户 (Peewee)")
                return None

        if found_person_id:
            required_fields = [
                "person_id",
                "platform",
                "user_id",
                "nickname",
                "user_cardname",
                "user_avatar",
                "person_name",
                "name_reason",
            ]
            valid_fields_to_get = [
                f for f in required_fields if f in PersonInfo._meta.fields or f in person_info_default
            ]

            person_data = await self.get_values(found_person_id, valid_fields_to_get)

            if person_data:
                final_result = {key: person_data.get(key) for key in required_fields}
                return final_result
            else:
                logger.warning(f"找到了 person_id '{found_person_id}' 但 get_values 返回空 (Peewee)")
                return None

        logger.error(f"逻辑错误：未能为 '{person_name}' 确定 person_id (Peewee)")
        return None


person_info_manager = PersonInfoManager()
