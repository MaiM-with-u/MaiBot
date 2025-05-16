from typing import Optional
from .personality import Personality
from .identity import Identity
import random
from rich.traceback import install
from src.config.config import global_config

install(extra_lines=3)


class Individuality:
    """个体特征管理类"""

    _instance = None

    def __init__(self):
        if Individuality._instance is not None:
            raise RuntimeError("Individuality 类是单例，请使用 get_instance() 方法获取实例。")

        # 正常初始化实例属性
        self.personality: Optional[Personality] = None
        self.identity: Optional[Identity] = None

        self.name = ""

    @classmethod
    def get_instance(cls) -> "Individuality":
        """获取Individuality单例实例

        Returns:
            Individuality: 单例实例
        """
        if cls._instance is None:
            # 实例不存在，调用 cls() 创建新实例
            # cls() 会调用 __init__
            # 因为此时 cls._instance 仍然是 None，__init__ 会正常执行初始化
            new_instance = cls()
            # 将新创建的实例赋值给类变量 _instance
            cls._instance = new_instance
        # 返回（新创建的或已存在的）单例实例
        return cls._instance

    def initialize(
        self,
        bot_nickname: str,
        personality_core: str,
        personality_sides: list,
        identity_detail: list,
        height: int,
        weight: int,
        age: int,
        gender: str,
        appearance: str,
    ) -> None:
        """初始化个体特征

        Args:
            bot_nickname: 机器人昵称
            personality_core: 人格核心特点
            personality_sides: 人格侧面描述
            identity_detail: 身份细节描述
            height: 身高（厘米）
            weight: 体重（千克）
            age: 年龄
            gender: 性别
            appearance: 外貌特征
        """
        # 初始化人格
        self.personality = Personality.initialize(
            bot_nickname=bot_nickname, personality_core=personality_core, personality_sides=personality_sides
        )

        # 初始化身份
        self.identity = Identity.initialize(
            identity_detail=identity_detail, height=height, weight=weight, age=age, gender=gender, appearance=appearance
        )

        self.name = bot_nickname

    def to_dict(self) -> dict:
        """将个体特征转换为字典格式"""
        return {
            "personality": self.personality.to_dict() if self.personality else None,
            "identity": self.identity.to_dict() if self.identity else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Individuality":
        """从字典创建个体特征实例"""
        instance = cls.get_instance()
        if data.get("personality"):
            instance.personality = Personality.from_dict(data["personality"])
        if data.get("identity"):
            instance.identity = Identity.from_dict(data["identity"])
        return instance

    def get_personality_prompt(self, level: int, x_person: int = 2) -> str:
        """
        获取人格特征的prompt

        Args:
            level (int): 详细程度 (1: 核心, 2: 核心+随机侧面, 3: 核心+所有侧面)
            x_person (int, optional): 人称代词 (0: 无人称, 1: 我, 2: 你). 默认为 2.

        Returns:
            str: 生成的人格prompt字符串
        """
        if x_person not in [0, 1, 2]:
            return "无效的人称代词，请使用 0 (无人称), 1 (我) 或 2 (你)。"
        if not self.personality:
            return "人格特征尚未初始化。"

        if x_person == 2:
            p_pronoun = "你"
            prompt_personality = f"{p_pronoun}{self.personality.personality_core}"
        elif x_person == 1:
            p_pronoun = "我"
            prompt_personality = f"{p_pronoun}{self.personality.personality_core}"
        else:  # x_person == 0
            # 对于无人称，直接描述核心特征
            prompt_personality = f"{self.personality.personality_core}"

        # 根据level添加人格侧面
        if level >= 2 and self.personality.personality_sides:
            personality_sides = list(self.personality.personality_sides)
            random.shuffle(personality_sides)
            if level == 2:
                prompt_personality += f"，有时也会{personality_sides[0]}"
            elif level == 3:
                sides_str = "、".join(personality_sides)
                prompt_personality += f"，有时也会{sides_str}"
        prompt_personality += "。"
        return prompt_personality

    def get_identity_prompt(self, level: int, x_person: int = 2) -> str:
        """
        获取身份特征的prompt

        Args:
            level (int): 详细程度 (1: 随机细节, 2: 所有细节+外貌年龄性别, 3: 同2)
            x_person (int, optional): 人称代词 (0: 无人称, 1: 我, 2: 你). 默认为 2.

        Returns:
            str: 生成的身份prompt字符串
        """
        if x_person not in [0, 1, 2]:
            return "无效的人称代词，请使用 0 (无人称), 1 (我) 或 2 (你)。"
        if not self.identity:
            return "身份特征尚未初始化。"

        if x_person == 2:
            i_pronoun = "你"
        elif x_person == 1:
            i_pronoun = "我"
        else:  # x_person == 0
            i_pronoun = ""  # 无人称

        identity_parts = []

        # 根据level添加身份细节
        if level >= 1 and self.identity.identity_detail:
            identity_detail = list(self.identity.identity_detail)
            random.shuffle(identity_detail)
            if level == 1:
                identity_parts.append(f"身份是{identity_detail[0]}")
            elif level >= 2:
                details_str = "、".join(identity_detail)
                identity_parts.append(f"身份是{details_str}")

        # 根据level添加其他身份信息
        if level >= 3:
            if self.identity.appearance:
                identity_parts.append(f"{self.identity.appearance}")
            if self.identity.age > 0:
                identity_parts.append(f"年龄大约{self.identity.age}岁")
            if self.identity.gender:
                identity_parts.append(f"性别是{self.identity.gender}")

        if identity_parts:
            details_str = "，".join(identity_parts)
            if x_person in [1, 2]:
                return f"{i_pronoun}，{details_str}。"
            else:  # x_person == 0
                # 无人称时，直接返回细节，不加代词和开头的逗号
                return f"{details_str}。"
        else:
            if x_person in [1, 2]:
                return f"{i_pronoun}的身份信息不完整。"
            else:  # x_person == 0
                return "身份信息不完整。"

    def get_prompt(self, level: int, x_person: int = 2) -> str:
        """
        获取合并的个体特征prompt

        Args:
            level (int): 详细程度 (1: 核心/随机细节, 2: 核心+随机侧面/全部细节, 3: 全部)
            x_person (int, optional): 人称代词 (0: 无人称, 1: 我, 2: 你). 默认为 2.

        Returns:
            str: 生成的合并prompt字符串
        """
        if x_person not in [0, 1, 2]:
            return "无效的人称代词，请使用 0 (无人称), 1 (我) 或 2 (你)。"

        if not self.personality or not self.identity:
            return "个体特征尚未完全初始化。"

        if global_config.personality_detail_level == 1:
            level = 1
        elif global_config.personality_detail_level == 2:
            level = 2
        elif global_config.personality_detail_level == 3:
            level = 3
        else:  # level = 0
            pass

        # 调用新的独立方法
        prompt_personality = self.get_personality_prompt(level, x_person)
        prompt_identity = self.get_identity_prompt(level, x_person)

        # 移除可能存在的错误信息，只合并有效的 prompt
        valid_prompts = []
        if "尚未初始化" not in prompt_personality and "无效的人称" not in prompt_personality:
            valid_prompts.append(prompt_personality)
        if (
            "尚未初始化" not in prompt_identity
            and "无效的人称" not in prompt_identity
            and "信息不完整" not in prompt_identity
        ):
            # 从身份 prompt 中移除代词和句号，以便更好地合并
            identity_content = prompt_identity
            if x_person == 2 and identity_content.startswith("你，"):
                identity_content = identity_content[2:]
            elif x_person == 1 and identity_content.startswith("我，"):
                identity_content = identity_content[2:]
            # 对于 x_person == 0，身份提示不带前缀，无需移除

            if identity_content.endswith("。"):
                identity_content = identity_content[:-1]
            valid_prompts.append(identity_content)

        # --- 合并 Prompt ---
        final_prompt = " ".join(valid_prompts)

        return final_prompt.strip()

    def get_traits(self, factor):
        """
        获取个体特征的特质
        """
        if factor == "openness":
            return self.personality.openness
        elif factor == "conscientiousness":
            return self.personality.conscientiousness
        elif factor == "extraversion":
            return self.personality.extraversion
        elif factor == "agreeableness":
            return self.personality.agreeableness
        elif factor == "neuroticism":
            return self.personality.neuroticism
        return None
