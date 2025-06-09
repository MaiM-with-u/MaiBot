from typing import Tuple, Dict, Any
from src.common.logger_manager import get_logger
from src.llm_models.utils_model import LLMRequest
from src.config.config import global_config

logger = get_logger("llm_api")

class LLMAPI:
    """LLM API模块
    
    提供了与LLM模型交互的功能
    """
    
    def get_available_models(self) -> Dict[str, Any]:
        """获取所有可用的模型配置

        Returns:
            Dict[str, Any]: 模型配置字典，key为模型名称，value为模型配置
        """
        if not hasattr(global_config, "model"):
            logger.error(f"{self.log_prefix} 无法获取模型列表：全局配置中未找到 model 配置")
            return {}
        
        models = global_config.model
            
        return models

    async def generate_with_model(
        self,
        prompt: str,
        model_config: Dict[str, Any],
        request_type: str = "plugin.generate",
        **kwargs
    ) -> Tuple[bool, str, str, str]:
        """使用指定模型生成内容

        Args:
            prompt: 提示词
            model_config: 模型配置（从 get_available_models 获取的模型配置）
            request_type: 请求类型标识
            **kwargs: 其他模型特定参数，如temperature、max_tokens等

        Returns:
            Tuple[bool, str, str, str]: (是否成功, 生成的内容, 推理过程, 模型名称)
        """
        try:
            logger.info(f"{self.log_prefix} 使用模型生成内容，提示词: {prompt[:100]}...")
            
            llm_request = LLMRequest(
                model=model_config,
                request_type=request_type,
                **kwargs
            )
            
            response, (reasoning, model_name) = await llm_request.generate_response_async(prompt)
            return True, response, reasoning, model_name
            
        except Exception as e:
            error_msg = f"生成内容时出错: {str(e)}"
            logger.error(f"{self.log_prefix} {error_msg}")
            return False, error_msg, "", "" 