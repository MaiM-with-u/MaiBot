"""
StyleLearner 数据库测试脚本
使用数据库中的expression数据测试style_learner功能
"""

import os
import sys
from typing import List, Dict, Tuple
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_recall_fscore_support

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.common.database.database_model import Expression, db
from src.express.style_learner import StyleLearnerManager
from src.common.logger import get_logger

logger = get_logger("style_learner_test")


class StyleLearnerDatabaseTest:
    """使用数据库数据测试StyleLearner"""
    
    def __init__(self, random_state: int = 42):
        self.random_state = random_state
        self.manager = StyleLearnerManager(model_save_path="data/test_style_models")
        
        # 测试结果
        self.test_results = {
            "total_samples": 0,
            "train_samples": 0,
            "test_samples": 0,
            "unique_styles": 0,
            "unique_chat_ids": 0,
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1_score": 0.0,
            "predictions": [],
            "ground_truth": [],
            "model_save_success": False,
            "model_save_path": self.manager.model_save_path
        }
    
    def load_data_from_database(self) -> List[Dict]:
        """
        从数据库加载expression数据
        
        Returns:
            List[Dict]: 包含up_content, style, chat_id的数据列表
        """
        try:
            # 连接数据库
            db.connect(reuse_if_open=True)
            
            # 查询所有expression数据
            expressions = Expression.select().where(
                (Expression.up_content.is_null(False)) &
                (Expression.style.is_null(False)) &
                (Expression.chat_id.is_null(False)) &
                (Expression.type == "style")
            )
            
            data = []
            for expr in expressions:
                if expr.up_content and expr.style and expr.chat_id:
                    data.append({
                        "up_content": expr.up_content,
                        "style": expr.style,
                        "chat_id": expr.chat_id,
                        "last_active_time": expr.last_active_time,
                        "context": expr.context,
                        "situation": expr.situation
                    })
            
            logger.info(f"从数据库加载了 {len(data)} 条expression数据")
            return data
            
        except Exception as e:
            logger.error(f"从数据库加载数据失败: {e}")
            return []
    
    def preprocess_data(self, data: List[Dict]) -> List[Dict]:
        """
        数据预处理
        
        Args:
            data: 原始数据
            
        Returns:
            List[Dict]: 预处理后的数据
        """
        # 过滤掉空值或过短的数据
        filtered_data = []
        for item in data:
            up_content = item["up_content"].strip()
            style = item["style"].strip()
            
            if len(up_content) >= 2 and len(style) >= 2:
                filtered_data.append({
                    "up_content": up_content,
                    "style": style,
                    "chat_id": item["chat_id"],
                    "last_active_time": item["last_active_time"],
                    "context": item["context"],
                    "situation": item["situation"]
                })
        
        logger.info(f"预处理后剩余 {len(filtered_data)} 条数据")
        return filtered_data
    
    def split_data(self, data: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """
        分割训练集和测试集
        训练集使用所有数据，测试集从训练集中随机选择5%
        
        Args:
            data: 预处理后的数据
            
        Returns:
            Tuple[List[Dict], List[Dict]]: (训练集, 测试集)
        """
        # 训练集使用所有数据
        train_data = data.copy()
        
        # 测试集从训练集中随机选择5%
        test_size = 0.05  # 5%
        test_data = train_test_split(
            train_data, test_size=test_size, random_state=self.random_state
        )[1]  # 只取测试集部分
        
        logger.info(f"数据分割完成: 训练集 {len(train_data)} 条, 测试集 {len(test_data)} 条")
        logger.info(f"训练集使用所有数据，测试集从训练集中随机选择 {test_size*100:.1f}%")
        return train_data, test_data
    
    def train_model(self, train_data: List[Dict]) -> None:
        """
        训练模型
        
        Args:
            train_data: 训练数据
        """
        logger.info("开始训练模型...")
        
        # 统计信息
        chat_ids = set()
        styles = set()
        
        for item in train_data:
            chat_id = item["chat_id"]
            up_content = item["up_content"]
            style = item["style"]
            
            chat_ids.add(chat_id)
            styles.add(style)
            
            # 学习映射关系
            success = self.manager.learn_mapping(chat_id, up_content, style)
            if not success:
                logger.warning(f"学习失败: {chat_id} - {up_content} -> {style}")
        
        self.test_results["train_samples"] = len(train_data)
        self.test_results["unique_styles"] = len(styles)
        self.test_results["unique_chat_ids"] = len(chat_ids)
        
        logger.info(f"训练完成: {len(train_data)} 个样本, {len(styles)} 种风格, {len(chat_ids)} 个聊天室")
        
        # 保存训练好的模型
        logger.info("开始保存训练好的模型...")
        save_success = self.manager.save_all_models()
        self.test_results["model_save_success"] = save_success
        
        if save_success:
            logger.info(f"所有模型已成功保存到: {self.manager.model_save_path}")
            print(f"✅ 模型已保存到: {self.manager.model_save_path}")
        else:
            logger.warning("部分模型保存失败")
            print(f"⚠️ 模型保存失败，请检查路径: {self.manager.model_save_path}")
    
    def test_model(self, test_data: List[Dict]) -> None:
        """
        测试模型
        
        Args:
            test_data: 测试数据
        """
        logger.info("开始测试模型...")
        
        predictions = []
        ground_truth = []
        correct_predictions = 0
        
        for item in test_data:
            chat_id = item["chat_id"]
            up_content = item["up_content"]
            true_style = item["style"]
            
            # 预测风格
            predicted_style, scores = self.manager.predict_style(chat_id, up_content, top_k=1)
            
            predictions.append(predicted_style)
            ground_truth.append(true_style)
            
            # 检查预测是否正确
            if predicted_style == true_style:
                correct_predictions += 1
            
            # 记录详细预测结果
            self.test_results["predictions"].append({
                "chat_id": chat_id,
                "up_content": up_content,
                "true_style": true_style,
                "predicted_style": predicted_style,
                "scores": scores
            })
        
        # 计算准确率
        accuracy = correct_predictions / len(test_data) if test_data else 0
        
        # 计算其他指标（需要处理None值）
        valid_predictions = [p for p in predictions if p is not None]
        valid_ground_truth = [gt for p, gt in zip(predictions, ground_truth, strict=False) if p is not None]
        
        if valid_predictions:
            precision, recall, f1, _ = precision_recall_fscore_support(
                valid_ground_truth, valid_predictions, average='weighted', zero_division=0
            )
        else:
            precision = recall = f1 = 0.0
        
        self.test_results["test_samples"] = len(test_data)
        self.test_results["accuracy"] = accuracy
        self.test_results["precision"] = precision
        self.test_results["recall"] = recall
        self.test_results["f1_score"] = f1
        
        logger.info(f"测试完成: 准确率 {accuracy:.4f}, 精确率 {precision:.4f}, 召回率 {recall:.4f}, F1分数 {f1:.4f}")
    
    def analyze_results(self) -> None:
        """分析测试结果"""
        logger.info("=== 测试结果分析 ===")
        
        print("\n📊 数据统计:")
        print(f"  总样本数: {self.test_results['total_samples']}")
        print(f"  训练样本数: {self.test_results['train_samples']}")
        print(f"  测试样本数: {self.test_results['test_samples']}")
        print(f"  唯一风格数: {self.test_results['unique_styles']}")
        print(f"  唯一聊天室数: {self.test_results['unique_chat_ids']}")
        
        print("\n🎯 模型性能:")
        print(f"  准确率: {self.test_results['accuracy']:.4f}")
        print(f"  精确率: {self.test_results['precision']:.4f}")
        print(f"  召回率: {self.test_results['recall']:.4f}")
        print(f"  F1分数: {self.test_results['f1_score']:.4f}")
        
        print("\n💾 模型保存:")
        save_status = "成功" if self.test_results['model_save_success'] else "失败"
        print(f"  保存状态: {save_status}")
        print(f"  保存路径: {self.test_results['model_save_path']}")
        
        # 分析各聊天室的性能
        chat_performance = {}
        for pred in self.test_results["predictions"]:
            chat_id = pred["chat_id"]
            if chat_id not in chat_performance:
                chat_performance[chat_id] = {"correct": 0, "total": 0}
            
            chat_performance[chat_id]["total"] += 1
            if pred["predicted_style"] == pred["true_style"]:
                chat_performance[chat_id]["correct"] += 1
        
        print("\n📈 各聊天室性能:")
        for chat_id, perf in chat_performance.items():
            accuracy = perf["correct"] / perf["total"] if perf["total"] > 0 else 0
            print(f"  {chat_id}: {accuracy:.4f} ({perf['correct']}/{perf['total']})")
        
        # 分析风格分布
        style_counts = {}
        for pred in self.test_results["predictions"]:
            style = pred["true_style"]
            style_counts[style] = style_counts.get(style, 0) + 1
        
        print("\n🎨 风格分布 (前10个):")
        sorted_styles = sorted(style_counts.items(), key=lambda x: x[1], reverse=True)
        for style, count in sorted_styles[:10]:
            print(f"  {style}: {count} 次")
    
    def show_sample_predictions(self, num_samples: int = 10) -> None:
        """显示样本预测结果"""
        print(f"\n🔍 样本预测结果 (前{num_samples}个):")
        
        for i, pred in enumerate(self.test_results["predictions"][:num_samples]):
            status = "✓" if pred["predicted_style"] == pred["true_style"] else "✗"
            print(f"\n  {i+1}. {status}")
            print(f"     聊天室: {pred['chat_id']}")
            print(f"     输入内容: {pred['up_content']}")
            print(f"     真实风格: {pred['true_style']}")
            print(f"     预测风格: {pred['predicted_style']}")
            if pred["scores"]:
                top_scores = dict(list(pred["scores"].items())[:3])
                print(f"     分数: {top_scores}")
    
    def save_results(self, output_file: str = "style_learner_test_results.txt") -> None:
        """保存测试结果到文件"""
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("StyleLearner 数据库测试结果\n")
                f.write("=" * 50 + "\n\n")
                
                f.write("数据统计:\n")
                f.write(f"  总样本数: {self.test_results['total_samples']}\n")
                f.write(f"  训练样本数: {self.test_results['train_samples']}\n")
                f.write(f"  测试样本数: {self.test_results['test_samples']}\n")
                f.write(f"  唯一风格数: {self.test_results['unique_styles']}\n")
                f.write(f"  唯一聊天室数: {self.test_results['unique_chat_ids']}\n\n")
                
                f.write("模型性能:\n")
                f.write(f"  准确率: {self.test_results['accuracy']:.4f}\n")
                f.write(f"  精确率: {self.test_results['precision']:.4f}\n")
                f.write(f"  召回率: {self.test_results['recall']:.4f}\n")
                f.write(f"  F1分数: {self.test_results['f1_score']:.4f}\n\n")
                
                f.write("模型保存:\n")
                save_status = "成功" if self.test_results['model_save_success'] else "失败"
                f.write(f"  保存状态: {save_status}\n")
                f.write(f"  保存路径: {self.test_results['model_save_path']}\n\n")
                
                f.write("详细预测结果:\n")
                for i, pred in enumerate(self.test_results["predictions"]):
                    status = "✓" if pred["predicted_style"] == pred["true_style"] else "✗"
                    f.write(f"{i+1}. {status} [{pred['chat_id']}] {pred['up_content']} -> {pred['predicted_style']} (真实: {pred['true_style']})\n")
            
            logger.info(f"测试结果已保存到 {output_file}")
            
        except Exception as e:
            logger.error(f"保存测试结果失败: {e}")
    
    def run_test(self) -> None:
        """运行完整测试"""
        logger.info("开始StyleLearner数据库测试...")
        
        # 1. 加载数据
        raw_data = self.load_data_from_database()
        if not raw_data:
            logger.error("没有加载到数据，测试终止")
            return
        
        # 2. 数据预处理
        processed_data = self.preprocess_data(raw_data)
        if not processed_data:
            logger.error("预处理后没有数据，测试终止")
            return
        
        self.test_results["total_samples"] = len(processed_data)
        
        # 3. 分割数据
        train_data, test_data = self.split_data(processed_data)
        
        # 4. 训练模型
        self.train_model(train_data)
        
        # 5. 测试模型
        self.test_model(test_data)
        
        # 6. 分析结果
        self.analyze_results()
        
        # 7. 显示样本预测
        self.show_sample_predictions(10)
        
        # 8. 保存结果
        self.save_results()
        
        logger.info("StyleLearner数据库测试完成!")


def main():
    """主函数"""
    print("StyleLearner 数据库测试脚本")
    print("=" * 50)
    
    # 创建测试实例
    test = StyleLearnerDatabaseTest(random_state=42)
    
    # 运行测试
    test.run_test()


if __name__ == "__main__":
    main()
