"""
多聊天室表达风格学习系统
支持为每个chat_id维护独立的表达模型，学习从up_content到style的映射
"""

import os
import pickle
import traceback
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import asyncio

from src.common.logger import get_logger
from .expressor_model.model import ExpressorModel

logger = get_logger("style_learner")


class StyleLearner:
    """
    单个聊天室的表达风格学习器
    学习从up_content到style的映射关系
    支持动态管理风格集合（最多2000个）
    """
    
    def __init__(self, chat_id: str, model_config: Optional[Dict] = None):
        self.chat_id = chat_id
        self.model_config = model_config or {
            "alpha": 0.5,
            "beta": 0.5, 
            "gamma": 0.99,  # 衰减因子，支持遗忘
            "vocab_size": 200000,
            "use_jieba": True
        }
        
        # 初始化表达模型
        self.expressor = ExpressorModel(**self.model_config)
        
        # 动态风格管理
        self.max_styles = 2000  # 每个chat_id最多2000个风格
        self.style_to_id: Dict[str, str] = {}  # style文本 -> style_id
        self.id_to_style: Dict[str, str] = {}  # style_id -> style文本
        self.id_to_situation: Dict[str, str] = {}  # style_id -> situation文本
        self.next_style_id = 0  # 下一个可用的style_id
        
        # 学习统计
        self.learning_stats = {
            "total_samples": 0,
            "style_counts": defaultdict(int),
            "last_update": None,
            "style_usage_frequency": defaultdict(int)  # 风格使用频率
        }
    
    def add_style(self, style: str, situation: str = None) -> bool:
        """
        动态添加一个新的风格
        
        Args:
            style: 风格文本
            situation: 对应的situation文本（可选）
            
        Returns:
            bool: 添加是否成功
        """
        try:
            # 检查是否已存在
            if style in self.style_to_id:
                logger.debug(f"[{self.chat_id}] 风格 '{style}' 已存在")
                return True
            
            # 检查是否超过最大限制
            if len(self.style_to_id) >= self.max_styles:
                logger.warning(f"[{self.chat_id}] 已达到最大风格数量限制 ({self.max_styles})")
                return False
            
            # 生成新的style_id
            style_id = f"style_{self.next_style_id}"
            self.next_style_id += 1
            
            # 添加到映射
            self.style_to_id[style] = style_id
            self.id_to_style[style_id] = style
            if situation:
                self.id_to_situation[style_id] = situation
            
            # 添加到expressor模型
            self.expressor.add_candidate(style_id, style, situation)
            
            logger.info(f"[{self.chat_id}] 已添加风格: '{style}' (ID: {style_id})" + 
                       (f", situation: '{situation}'" if situation else ""))
            return True
            
        except Exception as e:
            logger.error(f"[{self.chat_id}] 添加风格失败: {e}")
            return False
    
    def remove_style(self, style: str) -> bool:
        """
        删除一个风格
        
        Args:
            style: 要删除的风格文本
            
        Returns:
            bool: 删除是否成功
        """
        try:
            if style not in self.style_to_id:
                logger.warning(f"[{self.chat_id}] 风格 '{style}' 不存在")
                return False
            
            style_id = self.style_to_id[style]
            
            # 从映射中删除
            del self.style_to_id[style]
            del self.id_to_style[style_id]
            if style_id in self.id_to_situation:
                del self.id_to_situation[style_id]
            
            # 从expressor模型中删除（通过重新构建）
            self._rebuild_expressor()
            
            logger.info(f"[{self.chat_id}] 已删除风格: '{style}' (ID: {style_id})")
            return True
            
        except Exception as e:
            logger.error(f"[{self.chat_id}] 删除风格失败: {e}")
            return False
    
    def update_style(self, old_style: str, new_style: str) -> bool:
        """
        更新一个风格
        
        Args:
            old_style: 原风格文本
            new_style: 新风格文本
            
        Returns:
            bool: 更新是否成功
        """
        try:
            if old_style not in self.style_to_id:
                logger.warning(f"[{self.chat_id}] 原风格 '{old_style}' 不存在")
                return False
            
            if new_style in self.style_to_id and new_style != old_style:
                logger.warning(f"[{self.chat_id}] 新风格 '{new_style}' 已存在")
                return False
            
            style_id = self.style_to_id[old_style]
            
            # 更新映射
            del self.style_to_id[old_style]
            self.style_to_id[new_style] = style_id
            self.id_to_style[style_id] = new_style
            
            # 更新expressor模型（保留原有的situation）
            situation = self.id_to_situation.get(style_id)
            self.expressor.add_candidate(style_id, new_style, situation)
            
            logger.info(f"[{self.chat_id}] 已更新风格: '{old_style}' -> '{new_style}'")
            return True
            
        except Exception as e:
            logger.error(f"[{self.chat_id}] 更新风格失败: {e}")
            return False
    
    def add_styles_batch(self, styles: List[str], situations: List[str] = None) -> int:
        """
        批量添加风格
        
        Args:
            styles: 风格文本列表
            situations: 对应的situation文本列表（可选）
            
        Returns:
            int: 成功添加的数量
        """
        success_count = 0
        for i, style in enumerate(styles):
            situation = situations[i] if situations and i < len(situations) else None
            if self.add_style(style, situation):
                success_count += 1
        
        logger.info(f"[{self.chat_id}] 批量添加风格: {success_count}/{len(styles)} 成功")
        return success_count
    
    def get_all_styles(self) -> List[str]:
        """获取所有已注册的风格"""
        return list(self.style_to_id.keys())
    
    def get_style_count(self) -> int:
        """获取当前风格数量"""
        return len(self.style_to_id)
    
    def get_situation(self, style: str) -> Optional[str]:
        """
        获取风格对应的situation
        
        Args:
            style: 风格文本
            
        Returns:
            Optional[str]: 对应的situation，如果不存在则返回None
        """
        if style not in self.style_to_id:
            return None
        
        style_id = self.style_to_id[style]
        return self.id_to_situation.get(style_id)
    
    def get_style_info(self, style: str) -> Tuple[Optional[str], Optional[str]]:
        """
        获取风格的完整信息
        
        Args:
            style: 风格文本
            
        Returns:
            Tuple[Optional[str], Optional[str]]: (style_id, situation)
        """
        if style not in self.style_to_id:
            return None, None
        
        style_id = self.style_to_id[style]
        situation = self.id_to_situation.get(style_id)
        return style_id, situation
    
    def get_all_style_info(self) -> Dict[str, Tuple[str, Optional[str]]]:
        """
        获取所有风格的完整信息
        
        Returns:
            Dict[str, Tuple[str, Optional[str]]]: {style: (style_id, situation)}
        """
        result = {}
        for style, style_id in self.style_to_id.items():
            situation = self.id_to_situation.get(style_id)
            result[style] = (style_id, situation)
        return result
    
    def _rebuild_expressor(self):
        """重新构建expressor模型（删除风格后使用）"""
        try:
            # 重新创建expressor
            self.expressor = ExpressorModel(**self.model_config)
            
            # 重新添加所有风格和situation
            for style_id, style_text in self.id_to_style.items():
                situation = self.id_to_situation.get(style_id)
                self.expressor.add_candidate(style_id, style_text, situation)
            
            logger.debug(f"[{self.chat_id}] 已重新构建expressor模型")
            
        except Exception as e:
            logger.error(f"[{self.chat_id}] 重新构建expressor失败: {e}")
    
    def learn_mapping(self, up_content: str, style: str) -> bool:
        """
        学习一个up_content到style的映射
        如果style不存在，会自动添加
        
        Args:
            up_content: 输入内容
            style: 对应的style文本
            
        Returns:
            bool: 学习是否成功
        """
        try:
            # 如果style不存在，先添加它
            if style not in self.style_to_id:
                if not self.add_style(style):
                    logger.warning(f"[{self.chat_id}] 无法添加风格 '{style}'，学习失败")
                    return False
            
            # 获取style_id
            style_id = self.style_to_id[style]
            
            # 使用正反馈学习
            self.expressor.update_positive(up_content, style_id)
            
            # 更新统计
            self.learning_stats["total_samples"] += 1
            self.learning_stats["style_counts"][style_id] += 1
            self.learning_stats["style_usage_frequency"][style] += 1
            self.learning_stats["last_update"] = asyncio.get_event_loop().time()
            
            logger.debug(f"[{self.chat_id}] 学习映射: '{up_content}' -> '{style}'")
            return True
            
        except Exception as e:
            logger.error(f"[{self.chat_id}] 学习映射失败: {e}")
            traceback.print_exc()
            return False
    
    def predict_style(self, up_content: str, top_k: int = 5) -> Tuple[Optional[str], Dict[str, float]]:
        """
        根据up_content预测最合适的style
        
        Args:
            up_content: 输入内容
            top_k: 返回前k个候选
            
        Returns:
            Tuple[最佳style文本, 所有候选的分数]
        """
        try:
            best_style_id, scores = self.expressor.predict(up_content, k=top_k)
            
            if best_style_id is None:
                return None, {}
            
            # 将style_id转换为style文本
            best_style = self.id_to_style.get(best_style_id)
            
            # 转换所有分数
            style_scores = {}
            for sid, score in scores.items():
                style_text = self.id_to_style.get(sid)
                if style_text:
                    style_scores[style_text] = score
            
            return best_style, style_scores
            
        except Exception as e:
            logger.error(f"[{self.chat_id}] 预测style失败: {e}")
            traceback.print_exc()
            return None, {}
    
    def decay_learning(self, factor: Optional[float] = None) -> None:
        """
        对学习到的知识进行衰减（遗忘）
        
        Args:
            factor: 衰减因子，None则使用配置中的gamma
        """
        self.expressor.decay(factor)
        logger.debug(f"[{self.chat_id}] 执行知识衰减")
    
    def get_stats(self) -> Dict:
        """获取学习统计信息"""
        return {
            "chat_id": self.chat_id,
            "total_samples": self.learning_stats["total_samples"],
            "style_count": len(self.style_to_id),
            "max_styles": self.max_styles,
            "style_counts": dict(self.learning_stats["style_counts"]),
            "style_usage_frequency": dict(self.learning_stats["style_usage_frequency"]),
            "last_update": self.learning_stats["last_update"],
            "all_styles": list(self.style_to_id.keys())
        }
    
    def save(self, base_path: str) -> bool:
        """
        保存模型到文件
        
        Args:
            base_path: 基础路径，实际文件为 {base_path}/{chat_id}_style_model.pkl
        """
        try:
            os.makedirs(base_path, exist_ok=True)
            file_path = os.path.join(base_path, f"{self.chat_id}_style_model.pkl")
            
            # 保存模型和统计信息
            save_data = {
                "model_config": self.model_config,
                "style_to_id": self.style_to_id,
                "id_to_style": self.id_to_style,
                "id_to_situation": self.id_to_situation,
                "next_style_id": self.next_style_id,
                "max_styles": self.max_styles,
                "learning_stats": self.learning_stats
            }
            
            # 先保存expressor模型
            expressor_path = os.path.join(base_path, f"{self.chat_id}_expressor.pkl")
            self.expressor.save(expressor_path)
            
            # 保存其他数据
            with open(file_path, "wb") as f:
                pickle.dump(save_data, f)
            
            logger.info(f"[{self.chat_id}] 模型已保存到 {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"[{self.chat_id}] 保存模型失败: {e}")
            return False
    
    def load(self, base_path: str) -> bool:
        """
        从文件加载模型
        
        Args:
            base_path: 基础路径
        """
        try:
            file_path = os.path.join(base_path, f"{self.chat_id}_style_model.pkl")
            expressor_path = os.path.join(base_path, f"{self.chat_id}_expressor.pkl")
            
            if not os.path.exists(file_path) or not os.path.exists(expressor_path):
                logger.warning(f"[{self.chat_id}] 模型文件不存在，将使用默认配置")
                return False
            
            # 加载其他数据
            with open(file_path, "rb") as f:
                save_data = pickle.load(f)
            
            # 恢复配置和状态
            self.model_config = save_data["model_config"]
            self.style_to_id = save_data["style_to_id"]
            self.id_to_style = save_data["id_to_style"]
            self.id_to_situation = save_data.get("id_to_situation", {})  # 兼容旧版本
            self.next_style_id = save_data["next_style_id"]
            self.max_styles = save_data.get("max_styles", 2000)
            self.learning_stats = save_data["learning_stats"]
            
            # 重新创建expressor并加载
            self.expressor = ExpressorModel(**self.model_config)
            self.expressor.load(expressor_path)
            
            logger.info(f"[{self.chat_id}] 模型已从 {file_path} 加载")
            return True
            
        except Exception as e:
            logger.error(f"[{self.chat_id}] 加载模型失败: {e}")
            return False


class StyleLearnerManager:
    """
    多聊天室表达风格学习管理器
    为每个chat_id维护独立的StyleLearner实例
    每个chat_id可以动态管理自己的风格集合（最多2000个）
    """
    
    def __init__(self, model_save_path: str = "data/style_models"):
        self.model_save_path = model_save_path
        self.learners: Dict[str, StyleLearner] = {}
        
        # 自动保存配置
        self.auto_save_interval = 300  # 5分钟
        self._auto_save_task: Optional[asyncio.Task] = None
        
        logger.info("StyleLearnerManager 已初始化")
    
    def get_learner(self, chat_id: str, model_config: Optional[Dict] = None) -> StyleLearner:
        """
        获取或创建指定chat_id的学习器
        
        Args:
            chat_id: 聊天室ID
            model_config: 模型配置，None则使用默认配置
            
        Returns:
            StyleLearner实例
        """
        if chat_id not in self.learners:
            # 创建新的学习器
            learner = StyleLearner(chat_id, model_config)
            
            # 尝试加载已保存的模型
            learner.load(self.model_save_path)
            
            self.learners[chat_id] = learner
            logger.info(f"为 chat_id={chat_id} 创建新的StyleLearner")
        
        return self.learners[chat_id]
    
    def add_style(self, chat_id: str, style: str) -> bool:
        """
        为指定chat_id添加风格
        
        Args:
            chat_id: 聊天室ID
            style: 风格文本
            
        Returns:
            bool: 添加是否成功
        """
        learner = self.get_learner(chat_id)
        return learner.add_style(style)
    
    def remove_style(self, chat_id: str, style: str) -> bool:
        """
        为指定chat_id删除风格
        
        Args:
            chat_id: 聊天室ID
            style: 风格文本
            
        Returns:
            bool: 删除是否成功
        """
        learner = self.get_learner(chat_id)
        return learner.remove_style(style)
    
    def update_style(self, chat_id: str, old_style: str, new_style: str) -> bool:
        """
        为指定chat_id更新风格
        
        Args:
            chat_id: 聊天室ID
            old_style: 原风格文本
            new_style: 新风格文本
            
        Returns:
            bool: 更新是否成功
        """
        learner = self.get_learner(chat_id)
        return learner.update_style(old_style, new_style)
    
    def get_chat_styles(self, chat_id: str) -> List[str]:
        """
        获取指定chat_id的所有风格
        
        Args:
            chat_id: 聊天室ID
            
        Returns:
            List[str]: 风格列表
        """
        learner = self.get_learner(chat_id)
        return learner.get_all_styles()
    
    def learn_mapping(self, chat_id: str, up_content: str, style: str) -> bool:
        """
        学习一个映射关系
        
        Args:
            chat_id: 聊天室ID
            up_content: 输入内容
            style: 对应的style
            
        Returns:
            bool: 学习是否成功
        """
        learner = self.get_learner(chat_id)
        return learner.learn_mapping(up_content, style)
    
    def predict_style(self, chat_id: str, up_content: str, top_k: int = 5) -> Tuple[Optional[str], Dict[str, float]]:
        """
        预测最合适的style
        
        Args:
            chat_id: 聊天室ID
            up_content: 输入内容
            top_k: 返回前k个候选
            
        Returns:
            Tuple[最佳style, 所有候选分数]
        """
        learner = self.get_learner(chat_id)
        return learner.predict_style(up_content, top_k)
    
    def decay_all_learners(self, factor: Optional[float] = None) -> None:
        """
        对所有学习器执行衰减
        
        Args:
            factor: 衰减因子
        """
        for learner in self.learners.values():
            learner.decay_learning(factor)
        logger.info("已对所有学习器执行衰减")
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """获取所有学习器的统计信息"""
        return {chat_id: learner.get_stats() for chat_id, learner in self.learners.items()}
    
    def save_all_models(self) -> bool:
        """保存所有模型"""
        success_count = 0
        for learner in self.learners.values():
            if learner.save(self.model_save_path):
                success_count += 1
        
        logger.info(f"已保存 {success_count}/{len(self.learners)} 个模型")
        return success_count == len(self.learners)
    
    def load_all_models(self) -> int:
        """加载所有已保存的模型"""
        if not os.path.exists(self.model_save_path):
            return 0
        
        loaded_count = 0
        for filename in os.listdir(self.model_save_path):
            if filename.endswith("_style_model.pkl"):
                chat_id = filename.replace("_style_model.pkl", "")
                learner = StyleLearner(chat_id)
                if learner.load(self.model_save_path):
                    self.learners[chat_id] = learner
                    loaded_count += 1
        
        logger.info(f"已加载 {loaded_count} 个模型")
        return loaded_count
    
    async def start_auto_save(self) -> None:
        """启动自动保存任务"""
        if self._auto_save_task is None or self._auto_save_task.done():
            self._auto_save_task = asyncio.create_task(self._auto_save_loop())
            logger.info("已启动自动保存任务")
    
    async def stop_auto_save(self) -> None:
        """停止自动保存任务"""
        if self._auto_save_task and not self._auto_save_task.done():
            self._auto_save_task.cancel()
            try:
                await self._auto_save_task
            except asyncio.CancelledError:
                pass
            logger.info("已停止自动保存任务")
    
    async def _auto_save_loop(self) -> None:
        """自动保存循环"""
        while True:
            try:
                await asyncio.sleep(self.auto_save_interval)
                self.save_all_models()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"自动保存失败: {e}")


# 全局管理器实例
style_learner_manager = StyleLearnerManager()
