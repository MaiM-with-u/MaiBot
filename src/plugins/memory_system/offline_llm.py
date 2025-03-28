import asyncio
import os
import time
from typing import Tuple, Union

import aiohttp
import requests
from src.common.logger import get_module_logger

logger = get_module_logger("offline_llm")


class LLM_request_off:
    def __init__(self, model_name="deepseek-ai/DeepSeek-V3", **kwargs):
        self.model_name = model_name
        self.params = kwargs
        self.api_key = os.getenv("SILICONFLOW_KEY")
        self.base_url = os.getenv("SILICONFLOW_BASE_URL")

        if not self.api_key or not self.base_url:
            raise ValueError("环境变量未正确加载：SILICONFLOW_KEY 或 SILICONFLOW_BASE_URL 未设置")

        logger.info(f"API URL: {self.base_url}")  # 使用 logger 记录 base_url

    def generate_response(self, prompt: str) -> Union[str, Tuple[str, str]]:
        """根据输入的提示生成模型的响应"""
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        # 构建请求体
        data = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            **self.params,
        }

        # 发送请求到完整的 chat/completions 端点
        api_url = f"{self.base_url.rstrip('/')}/chat/completions"
        logger.info(f"Request URL: {api_url}")  # 记录请求的 URL

        max_retries = 3
        base_wait_time = 15  # 基础等待时间（秒）

        for retry in range(max_retries):
            try:
                response = requests.post(api_url, headers=headers, json=data)

                if response.status_code == 429:
                    wait_time = base_wait_time * (2**retry)  # 指数退避
                    logger.warning(f"遇到请求限制(429)，等待{wait_time}秒后重试...")
                    time.sleep(wait_time)
                    continue

                response.raise_for_status()  # 检查其他响应状态

                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    reasoning_content = result["choices"][0]["message"].get("reasoning_content", "")
                    return content, reasoning_content
                return "没有返回结果", ""

            except Exception as e:
                if retry < max_retries - 1:  # 如果还有重试机会
                    wait_time = base_wait_time * (2**retry)
                    logger.error(f"[回复]请求失败，等待{wait_time}秒后重试... 错误: {str(e)}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"请求失败: {str(e)}")
                    return f"请求失败: {str(e)}", ""

        logger.error("达到最大重试次数，请求仍然失败")
        return "达到最大重试次数，请求仍然失败", ""

    async def generate_response_async(self, prompt: str) -> Union[str, Tuple[str, str]]:
        """异步方式根据输入的提示生成模型的响应"""
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        # 构建请求体
        data = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            **self.params,
        }

        # 发送请求到完整的 chat/completions 端点
        api_url = f"{self.base_url.rstrip('/')}/chat/completions"
        logger.info(f"Request URL: {api_url}")  # 记录请求的 URL

        max_retries = 3
        base_wait_time = 15

        async with aiohttp.ClientSession() as session:
            for retry in range(max_retries):
                try:
                    async with session.post(api_url, headers=headers, json=data) as response:
                        if response.status == 429:
                            wait_time = base_wait_time * (2**retry)  # 指数退避
                            logger.warning(f"遇到请求限制(429)，等待{wait_time}秒后重试...")
                            await asyncio.sleep(wait_time)
                            continue

                        response.raise_for_status()  # 检查其他响应状态

                        result = await response.json()
                        if "choices" in result and len(result["choices"]) > 0:
                            content = result["choices"][0]["message"]["content"]
                            reasoning_content = result["choices"][0]["message"].get("reasoning_content", "")
                            return content, reasoning_content
                        return "没有返回结果", ""

                except Exception as e:
                    if retry < max_retries - 1:  # 如果还有重试机会
                        wait_time = base_wait_time * (2**retry)
                        logger.error(f"[回复]请求失败，等待{wait_time}秒后重试... 错误: {str(e)}")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"请求失败: {str(e)}")
                        return f"请求失败: {str(e)}", ""

            logger.error("达到最大重试次数，请求仍然失败")
            return "达到最大重试次数，请求仍然失败", ""
