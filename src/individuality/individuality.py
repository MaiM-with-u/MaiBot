from typing import Optional
from .personality import Personality
from .identity import Identity
from .expression_style import PersonalityExpression
import random
from rich.traceback import install

install(extra_lines=3)


class Individuality:
    """个体特征管理类"""

    def __init__(self):
        # 正常初始化实例属性
        self.personality: Optional[Personality] = None
        self.identity: Optional[Identity] = None
        self.express_style: PersonalityExpression = PersonalityExpression()

        self.name = ""

    async def initialize(
        self,
        bot_nickname: str,
        personality_core: str,
        personality_sides: list,
        identity_detail: list,
    ) -> None:
        """初始化个体特征

        Args:
            bot_nickname: 机器人昵称
            personality_core: 人格核心特点
            personality_sides: 人格侧面描述
            identity_detail: 身份细节描述
        """
        # 初始化人格
        self.personality = Personality.initialize(
            bot_nickname=bot_nickname, personality_core=personality_core, personality_sides=personality_sides
        )

        # 初始化身份
        self.identity = Identity(identity_detail=identity_detail)

        await self.express_style.extract_and_store_personality_expressions()

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
        instance = cls()
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
            level (int): 详细程度 (1: 随机细节, 2: 所有细节, 3: 同2)
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
                identity_parts.append(f"{identity_detail[0]}")
            elif level >= 2:
                details_str = "、".join(identity_detail)
                identity_parts.append(f"{details_str}")

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


individuality = Individuality()
