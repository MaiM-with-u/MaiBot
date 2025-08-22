import hashlib
import asyncio
import json
import time
import random
import math

from json_repair import repair_json
from typing import Union, Optional

from src.common.logger import get_logger
from src.common.database.database import db
from src.common.database.database_model import PersonInfo
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config, model_config


logger = get_logger("person_info")


def get_person_id(platform: str, user_id: Union[int, str]) -> str:
    """获取唯一id"""
    if "-" in platform:
        platform = platform.split("-")[1]
    components = [platform, str(user_id)]
    key = "_".join(components)
    return hashlib.md5(key.encode()).hexdigest()


def get_person_id_by_person_name(person_name: str) -> str:
    """根据用户名获取用户ID"""
    try:
        record = PersonInfo.get_or_none(PersonInfo.person_name == person_name)
        return record.person_id if record else ""
    except Exception as e:
        logger.error(f"根据用户名 {person_name} 获取用户ID时出错 (Peewee): {e}")
        return ""


def is_person_known(person_id: str = None, user_id: str = None, platform: str = None, person_name: str = None) -> bool:  # type: ignore
    if person_id:
        person = PersonInfo.get_or_none(PersonInfo.person_id == person_id)
        return person.is_known if person else False
    elif user_id and platform:
        person_id = get_person_id(platform, user_id)
        person = PersonInfo.get_or_none(PersonInfo.person_id == person_id)
        return person.is_known if person else False
    elif person_name:
        person_id = get_person_id_by_person_name(person_name)
        person = PersonInfo.get_or_none(PersonInfo.person_id == person_id)
        return person.is_known if person else False
    else:
        return False


def get_category_from_memory(memory_point: str) -> Optional[str]:
    """从记忆点中获取分类"""
    # 按照最左边的:符号进行分割，返回分割后的第一个部分作为分类
    if not isinstance(memory_point, str):
        return None
    parts = memory_point.split(":", 1)
    return parts[0].strip() if len(parts) > 1 else None


def get_weight_from_memory(memory_point: str) -> float:
    """从记忆点中获取权重"""
    # 按照最右边的:符号进行分割，返回分割后的最后一个部分作为权重
    if not isinstance(memory_point, str):
        return -math.inf
    parts = memory_point.rsplit(":", 1)
    if len(parts) <= 1:
        return -math.inf
    try:
        return float(parts[-1].strip())
    except Exception:
        return -math.inf


def get_memory_content_from_memory(memory_point: str) -> str:
    """从记忆点中获取记忆内容"""
    # 按:进行分割，去掉第一段和最后一段，返回中间部分作为记忆内容
    if not isinstance(memory_point, str):
        return ""
    parts = memory_point.split(":")
    return ":".join(parts[1:-1]).strip() if len(parts) > 2 else ""


def calculate_string_similarity(s1: str, s2: str) -> float:
    """
    计算两个字符串的相似度

    Args:
        s1: 第一个字符串
        s2: 第二个字符串

    Returns:
        float: 相似度，范围0-1，1表示完全相同
    """
    if s1 == s2:
        return 1.0

    if not s1 or not s2:
        return 0.0

    # 计算Levenshtein距离

    distance = levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))

    # 计算相似度：1 - (编辑距离 / 最大长度)
    similarity = 1 - (distance / max_len if max_len > 0 else 0)
    return similarity


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    计算两个字符串的编辑距离

    Args:
        s1: 第一个字符串
        s2: 第二个字符串

    Returns:
        int: 编辑距离
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


class Person:
    @classmethod
    def register_person(cls, platform: str, user_id: str, nickname: str):
        """
        注册新用户的类方法
        必须输入 platform、user_id 和 nickname 参数

        Args:
            platform: 平台名称
            user_id: 用户ID
            nickname: 用户昵称

        Returns:
            Person: 新注册的Person实例
        """
        if not platform or not user_id or not nickname:
            logger.error("注册用户失败：platform、user_id 和 nickname 都是必需参数")
            return None

        # 生成唯一的person_id
        person_id = get_person_id(platform, user_id)

        if is_person_known(person_id=person_id):
            logger.debug(f"用户 {nickname} 已存在")
            return Person(person_id=person_id)

        # 创建Person实例
        person = cls.__new__(cls)

        # 设置基本属性
        person.person_id = person_id
        person.platform = platform
        person.user_id = user_id
        person.nickname = nickname

        # 初始化默认值
        person.is_known = True  # 注册后立即标记为已认识
        person.person_name = nickname  # 使用nickname作为初始person_name
        person.name_reason = "用户注册时设置的昵称"
        person.know_times = 1
        person.know_since = time.time()
        person.last_know = time.time()
        person.memory_points = []

        # 初始化性格特征相关字段
        person.attitude_to_me = 0
        person.attitude_to_me_confidence = 1

        person.neuroticism = 5
        person.neuroticism_confidence = 1

        person.friendly_value = 50
        person.friendly_value_confidence = 1

        person.rudeness = 50
        person.rudeness_confidence = 1

        person.conscientiousness = 50
        person.conscientiousness_confidence = 1

        person.likeness = 50
        person.likeness_confidence = 1

        # 同步到数据库
        person.sync_to_database()

        logger.info(f"成功注册新用户：{person_id}，平台：{platform}，昵称：{nickname}")

        return person

    def __init__(self, platform: str = "", user_id: str = "", person_id: str = "", person_name: str = ""):
        if platform == global_config.bot.platform and user_id == global_config.bot.qq_account:
            self.is_known = True
            self.person_id = get_person_id(platform, user_id)
            self.user_id = user_id
            self.platform = platform
            self.nickname = global_config.bot.nickname
            self.person_name = global_config.bot.nickname
            return

        self.user_id = ""
        self.platform = ""

        if person_id:
            self.person_id = person_id
        elif person_name:
            self.person_id = get_person_id_by_person_name(person_name)
            if not self.person_id:
                self.is_known = False
                logger.warning(f"根据用户名 {person_name} 获取用户ID时，不存在用户{person_name}")
                return
        elif platform and user_id:
            self.person_id = get_person_id(platform, user_id)
            self.user_id = user_id
            self.platform = platform
        else:
            logger.error("Person 初始化失败，缺少必要参数")
            raise ValueError("Person 初始化失败，缺少必要参数")

        if not is_person_known(person_id=self.person_id):
            self.is_known = False
            logger.debug(f"用户 {platform}:{user_id}:{person_name}:{person_id} 尚未认识")
            self.person_name = f"未知用户{self.person_id[:4]}"
            return
            # raise ValueError(f"用户 {platform}:{user_id}:{person_name}:{person_id} 尚未认识")

        self.is_known = False

        # 初始化默认值
        self.nickname = ""
        self.person_name: Optional[str] = None
        self.name_reason: Optional[str] = None
        self.know_times = 0
        self.know_since = None
        self.last_know = None
        self.memory_points = []

        # 初始化性格特征相关字段
        self.attitude_to_me: float = 0
        self.attitude_to_me_confidence: float = 1

        self.neuroticism: float = 5
        self.neuroticism_confidence: float = 1

        self.friendly_value: float = 50
        self.friendly_value_confidence: float = 1

        self.rudeness: float = 50
        self.rudeness_confidence: float = 1

        self.conscientiousness: float = 50
        self.conscientiousness_confidence: float = 1

        self.likeness: float = 50
        self.likeness_confidence: float = 1

        # 从数据库加载数据
        self.load_from_database()

    def del_memory(self, category: str, memory_content: str, similarity_threshold: float = 0.95):
        """
        删除指定分类和记忆内容的记忆点

        Args:
            category: 记忆分类
            memory_content: 要删除的记忆内容
            similarity_threshold: 相似度阈值，默认0.95（95%）

        Returns:
            int: 删除的记忆点数量
        """
        if not self.memory_points:
            return 0

        deleted_count = 0
        memory_points_to_keep = []

        for memory_point in self.memory_points:
            # 跳过None值
            if memory_point is None:
                continue
            # 解析记忆点
            parts = memory_point.split(":", 2)  # 最多分割2次，保留记忆内容中的冒号
            if len(parts) < 3:
                # 格式不正确，保留原样
                memory_points_to_keep.append(memory_point)
                continue

            memory_category = parts[0].strip()
            memory_text = parts[1].strip()
            memory_weight = parts[2].strip()

            # 检查分类是否匹配
            if memory_category != category:
                memory_points_to_keep.append(memory_point)
                continue

            # 计算记忆内容的相似度
            similarity = calculate_string_similarity(memory_content, memory_text)

            # 如果相似度达到阈值，则删除（不添加到保留列表）
            if similarity >= similarity_threshold:
                deleted_count += 1
                logger.debug(f"删除记忆点: {memory_point} (相似度: {similarity:.4f})")
            else:
                memory_points_to_keep.append(memory_point)

        # 更新memory_points
        self.memory_points = memory_points_to_keep

        # 同步到数据库
        if deleted_count > 0:
            self.sync_to_database()
            logger.info(f"成功删除 {deleted_count} 个记忆点，分类: {category}")

        return deleted_count

    def get_all_category(self):
        category_list = []
        for memory in self.memory_points:
            if memory is None:
                continue
            category = get_category_from_memory(memory)
            if category and category not in category_list:
                category_list.append(category)
        return category_list

    def get_memory_list_by_category(self, category: str):
        memory_list = []
        for memory in self.memory_points:
            if memory is None:
                continue
            if get_category_from_memory(memory) == category:
                memory_list.append(memory)
        return memory_list

    def get_random_memory_by_category(self, category: str, num: int = 1):
        memory_list = self.get_memory_list_by_category(category)
        if len(memory_list) < num:
            return memory_list
        return random.sample(memory_list, num)

    def load_from_database(self):
        """从数据库加载个人信息数据"""
        try:
            # 查询数据库中的记录
            record = PersonInfo.get_or_none(PersonInfo.person_id == self.person_id)

            if record:
                self.user_id = record.user_id or ""
                self.platform = record.platform or ""
                self.is_known = record.is_known or False
                self.nickname = record.nickname or ""
                self.person_name = record.person_name or self.nickname
                self.name_reason = record.name_reason or None
                self.know_times = record.know_times or 0

                # 处理points字段（JSON格式的列表）
                if record.memory_points:
                    try:
                        loaded_points = json.loads(record.memory_points)
                        # 过滤掉None值，确保数据质量
                        if isinstance(loaded_points, list):
                            self.memory_points = [point for point in loaded_points if point is not None]
                        else:
                            self.memory_points = []
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"解析用户 {self.person_id} 的points字段失败，使用默认值")
                        self.memory_points = []
                else:
                    self.memory_points = []

                # 加载性格特征相关字段
                if record.attitude_to_me and not isinstance(record.attitude_to_me, str):
                    self.attitude_to_me = record.attitude_to_me

                if record.attitude_to_me_confidence is not None:
                    self.attitude_to_me_confidence = float(record.attitude_to_me_confidence)

                if record.friendly_value is not None:
                    self.friendly_value = float(record.friendly_value)

                if record.friendly_value_confidence is not None:
                    self.friendly_value_confidence = float(record.friendly_value_confidence)

                if record.rudeness is not None:
                    self.rudeness = float(record.rudeness)

                if record.rudeness_confidence is not None:
                    self.rudeness_confidence = float(record.rudeness_confidence)

                if record.neuroticism and not isinstance(record.neuroticism, str):
                    self.neuroticism = float(record.neuroticism)

                if record.neuroticism_confidence is not None:
                    self.neuroticism_confidence = float(record.neuroticism_confidence)

                if record.conscientiousness is not None:
                    self.conscientiousness = float(record.conscientiousness)

                if record.conscientiousness_confidence is not None:
                    self.conscientiousness_confidence = float(record.conscientiousness_confidence)

                if record.likeness is not None:
                    self.likeness = float(record.likeness)

                if record.likeness_confidence is not None:
                    self.likeness_confidence = float(record.likeness_confidence)

                logger.debug(f"已从数据库加载用户 {self.person_id} 的信息")
            else:
                self.sync_to_database()
                logger.info(f"用户 {self.person_id} 在数据库中不存在，使用默认值并创建")

        except Exception as e:
            logger.error(f"从数据库加载用户 {self.person_id} 信息时出错: {e}")
            # 出错时保持默认值

    def sync_to_database(self):
        """将所有属性同步回数据库"""
        if not self.is_known:
            return
        try:
            # 准备数据
            data = {
                "person_id": self.person_id,
                "is_known": self.is_known,
                "platform": self.platform,
                "user_id": self.user_id,
                "nickname": self.nickname,
                "person_name": self.person_name,
                "name_reason": self.name_reason,
                "know_times": self.know_times,
                "know_since": self.know_since,
                "last_know": self.last_know,
                "memory_points": json.dumps(
                    [point for point in self.memory_points if point is not None], ensure_ascii=False
                )
                if self.memory_points
                else json.dumps([], ensure_ascii=False),
                "attitude_to_me": self.attitude_to_me,
                "attitude_to_me_confidence": self.attitude_to_me_confidence,
                "friendly_value": self.friendly_value,
                "friendly_value_confidence": self.friendly_value_confidence,
                "rudeness": self.rudeness,
                "rudeness_confidence": self.rudeness_confidence,
                "neuroticism": self.neuroticism,
                "neuroticism_confidence": self.neuroticism_confidence,
                "conscientiousness": self.conscientiousness,
                "conscientiousness_confidence": self.conscientiousness_confidence,
                "likeness": self.likeness,
                "likeness_confidence": self.likeness_confidence,
            }

            # 检查记录是否存在
            record = PersonInfo.get_or_none(PersonInfo.person_id == self.person_id)

            if record:
                # 更新现有记录
                for field, value in data.items():
                    if hasattr(record, field):
                        setattr(record, field, value)
                record.save()
                logger.debug(f"已同步用户 {self.person_id} 的信息到数据库")
            else:
                # 创建新记录
                PersonInfo.create(**data)
                logger.debug(f"已创建用户 {self.person_id} 的信息到数据库")

        except Exception as e:
            logger.error(f"同步用户 {self.person_id} 信息到数据库时出错: {e}")

    def build_relationship(self):
        if not self.is_known:
            return ""
        # 构建points文本

        nickname_str = ""
        if self.person_name != self.nickname:
            nickname_str = f"(ta在{self.platform}上的昵称是{self.nickname})"

        relation_info = ""

        attitude_info = ""
        if self.attitude_to_me:
            if self.attitude_to_me > 8:
                attitude_info = f"{self.person_name}对你的态度十分好,"
            elif self.attitude_to_me > 5:
                attitude_info = f"{self.person_name}对你的态度较好,"

            if self.attitude_to_me < -8:
                attitude_info = f"{self.person_name}对你的态度十分恶劣,"
            elif self.attitude_to_me < -4:
                attitude_info = f"{self.person_name}对你的态度不好,"
            elif self.attitude_to_me < 0:
                attitude_info = f"{self.person_name}对你的态度一般,"

        neuroticism_info = ""
        if self.neuroticism:
            if self.neuroticism > 8:
                neuroticism_info = f"{self.person_name}的情绪十分活跃，容易情绪化,"
            elif self.neuroticism > 6:
                neuroticism_info = f"{self.person_name}的情绪比较活跃,"
            elif self.neuroticism > 4:
                neuroticism_info = ""
            elif self.neuroticism > 2:
                neuroticism_info = f"{self.person_name}的情绪比较稳定,"
            else:
                neuroticism_info = f"{self.person_name}的情绪非常稳定,毫无波动"

        points_text = ""
        category_list = self.get_all_category()
        for category in category_list:
            random_memory = self.get_random_memory_by_category(category, 1)[0]
            if random_memory:
                points_text = f"有关 {category} 的记忆：{get_memory_content_from_memory(random_memory)}"
                break

        points_info = ""
        if points_text:
            points_info = f"你还记得有关{self.person_name}的最近记忆：{points_text}"

        if not (nickname_str or attitude_info or neuroticism_info or points_info):
            return ""
        relation_info = f"{self.person_name}:{nickname_str}{attitude_info}{neuroticism_info}{points_info}"

        return relation_info


class PersonInfoManager:
    def __init__(self):
        self.person_name_list = {}
        self.qv_name_llm = LLMRequest(model_set=model_config.model_task_config.utils, request_type="relation.qv_name")
        try:
            db.connect(reuse_if_open=True)
            # 设置连接池参数
            if hasattr(db, "execute_sql"):
                # 设置SQLite优化参数
                db.execute_sql("PRAGMA cache_size = -64000")  # 64MB缓存
                db.execute_sql("PRAGMA temp_store = memory")  # 临时存储在内存中
                db.execute_sql("PRAGMA mmap_size = 268435456")  # 256MB内存映射
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
    def _extract_json_from_text(text: str) -> dict:
        """从文本中提取JSON数据的高容错方法"""
        try:
            fixed_json = repair_json(text)
            if isinstance(fixed_json, str):
                parsed_json = json.loads(fixed_json)
            else:
                parsed_json = fixed_json

            if isinstance(parsed_json, list) and parsed_json:
                parsed_json = parsed_json[0]

            if isinstance(parsed_json, dict):
                return parsed_json

        except Exception as e:
            logger.warning(f"JSON提取失败: {e}")

        logger.warning(f"无法从文本中提取有效的JSON字典: {text}")
        logger.info(f"文本: {text}")
        return {"nickname": "", "reason": ""}

    async def _generate_unique_person_name(self, base_name: str) -> str:
        """生成唯一的 person_name，如果存在重复则添加数字后缀"""
        # 处理空昵称的情况
        if not base_name or base_name.isspace():
            base_name = "空格"

        # 检查基础名称是否已存在
        if base_name not in self.person_name_list.values():
            return base_name

        # 如果存在，添加数字后缀
        counter = 1
        while True:
            new_name = f"{base_name}[{counter}]"
            if new_name not in self.person_name_list.values():
                return new_name
            counter += 1

    async def qv_person_name(
        self, person_id: str, user_nickname: str, user_cardname: str, user_avatar: str, request: str = ""
    ):
        """给某个用户取名"""
        if not person_id:
            logger.debug("取名失败：person_id不能为空")
            return None

        person = Person(person_id=person_id)
        old_name = person.person_name
        old_reason = person.name_reason

        max_retries = 8
        current_try = 0
        existing_names_str = ""
        current_name_set = set(self.person_name_list.values())

        while current_try < max_retries:
            bot_name = global_config.bot.nickname

            qv_name_prompt = f"你是{bot_name}，一个伪装成人类的AI，你不能让别人发现这一点，"
            qv_name_prompt += f"现在你想给一个用户取一个昵称，用户的qq昵称是{user_nickname}，"
            qv_name_prompt += f"用户的qq群昵称名是{user_cardname}，"
            if user_avatar:
                qv_name_prompt += f"用户的qq头像是{user_avatar}，"
            if old_name:
                qv_name_prompt += f"你之前叫他{old_name}，是因为{old_reason}，"

            qv_name_prompt += f"\n其他取名的要求是：{request}，不要太浮夸，简短，"
            qv_name_prompt += "\n请根据以上用户信息，想想你叫他什么比较好，不要太浮夸，请最好使用用户的qq昵称或群昵称原文，可以稍作修改，优先使用原文。优先使用用户的qq昵称或者群昵称原文。"

            if existing_names_str:
                qv_name_prompt += f"\n请注意，以下名称已被你尝试过或已知存在，请避免：{existing_names_str}。\n"

            if len(current_name_set) < 50 and current_name_set:
                qv_name_prompt += f"已知的其他昵称有: {', '.join(list(current_name_set)[:10])}等。\n"

            qv_name_prompt += "请用json给出你的想法，并给出理由，示例如下："
            qv_name_prompt += """{
                "nickname": "昵称",
                "reason": "理由"
            }"""
            response, _ = await self.qv_name_llm.generate_response_async(qv_name_prompt)
            # logger.info(f"取名提示词：{qv_name_prompt}\n取名回复：{response}")
            result = self._extract_json_from_text(response)

            if not result or not result.get("nickname"):
                logger.error("生成的昵称为空或结果格式不正确，重试中...")
                current_try += 1
                continue

            generated_nickname = result["nickname"]

            is_duplicate = False
            if generated_nickname in current_name_set:
                is_duplicate = True
                logger.info(f"尝试给用户{user_nickname} {person_id} 取名，但是 {generated_nickname} 已存在，重试中...")
            else:

                def _db_check_name_exists_sync(name_to_check):
                    return PersonInfo.select().where(PersonInfo.person_name == name_to_check).exists()

                if await asyncio.to_thread(_db_check_name_exists_sync, generated_nickname):
                    is_duplicate = True
                    current_name_set.add(generated_nickname)

            if not is_duplicate:
                person.person_name = generated_nickname
                person.name_reason = result.get("reason", "未提供理由")
                person.sync_to_database()

                logger.info(
                    f"成功给用户{user_nickname} {person_id} 取名 {generated_nickname}，理由：{result.get('reason', '未提供理由')}"
                )

                self.person_name_list[person_id] = generated_nickname
                return result
            else:
                if existing_names_str:
                    existing_names_str += "、"
                existing_names_str += generated_nickname
                logger.debug(f"生成的昵称 {generated_nickname} 已存在，重试中...")
                current_try += 1

        # 如果多次尝试后仍未成功，使用唯一的 user_nickname 作为默认值
        unique_nickname = await self._generate_unique_person_name(user_nickname)
        logger.warning(f"在{max_retries}次尝试后未能生成唯一昵称，使用默认昵称 {unique_nickname}")
        person.person_name = unique_nickname
        person.name_reason = "使用用户原始昵称作为默认值"
        person.sync_to_database()
        self.person_name_list[person_id] = unique_nickname
        return {"nickname": unique_nickname, "reason": "使用用户原始昵称作为默认值"}


person_info_manager = PersonInfoManager()
