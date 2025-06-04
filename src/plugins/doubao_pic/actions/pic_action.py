import asyncio
import json
import urllib.request
import urllib.error
import base64  # 新增：用于Base64编码
import traceback  # 新增：用于打印堆栈跟踪
from typing import Tuple
from src.chat.focus_chat.planners.actions.plugin_action import PluginAction, register_action
from src.common.logger_manager import get_logger
from .generate_pic_config import generate_config

logger = get_logger("pic_action")

# 当此模块被加载时，尝试生成配置文件（如果它不存在）
# 注意：在某些插件加载机制下，这可能会在每次机器人启动或插件重载时执行
# 考虑是否需要更复杂的逻辑来决定何时运行 (例如，仅在首次安装时)
generate_config()


@register_action
class PicAction(PluginAction):
    """根据描述使用火山引擎HTTP API生成图片的动作处理类"""

    action_name = "pic_action"
    action_description = (
        "可以根据特定的描述，生成并发送一张图片，如果没提供描述，就根据聊天内容生成,你可以立刻画好，不用等待"
    )
    action_parameters = {
        "description": "图片描述，输入你想要生成并发送的图片的描述，必填",
        "size": "图片尺寸，例如 '1024x1024' (可选, 默认从配置或 '1024x1024')",
    }
    action_require = [
        "当有人让你画东西时使用，你可以立刻画好，不用等待",
        "当有人要求你生成并发送一张图片时使用",
        "当有人让你画一张图时使用",
    ]
    default = False
    action_config_file_name = "pic_action_config.toml"

    def __init__(
        self,
        action_data: dict,
        reasoning: str,
        cycle_timers: dict,
        thinking_id: str,
        global_config: dict = None,
        **kwargs,
    ):
        super().__init__(action_data, reasoning, cycle_timers, thinking_id, global_config, **kwargs)

        logger.info(f"{self.log_prefix} 开始绘图！原因是：{self.reasoning}")

        http_base_url = self.config.get("base_url")
        http_api_key = self.config.get("volcano_generate_api_key")

        if not (http_base_url and http_api_key):
            logger.error(
                f"{self.log_prefix} PicAction初始化, 但HTTP配置 (base_url 或 volcano_generate_api_key) 缺失. HTTP图片生成将失败."
            )
        else:
            logger.info(f"{self.log_prefix} HTTP方式初始化完成. Base URL: {http_base_url}, API Key已配置.")

    # _restore_env_vars 方法不再需要，已移除

    async def process(self) -> Tuple[bool, str]:
        """处理图片生成动作（通过HTTP API）"""
        logger.info(f"{self.log_prefix} 执行 pic_action (HTTP): {self.reasoning}")

        http_base_url = self.config.get("base_url")
        http_api_key = self.config.get("volcano_generate_api_key")

        if not (http_base_url and http_api_key):
            error_msg = "抱歉，图片生成功能所需的HTTP配置（如API地址或密钥）不完整，无法提供服务。"
            await self.send_message_by_expressor(error_msg)
            logger.error(f"{self.log_prefix} HTTP调用配置缺失: base_url 或 volcano_generate_api_key.")
            return False, "HTTP配置不完整"

        description = self.action_data.get("description")
        if not description:
            logger.warning(f"{self.log_prefix} 图片描述为空，无法生成图片。")
            await self.send_message_by_expressor("你需要告诉我想要画什么样的图片哦~")
            return False, "图片描述为空"

        default_model = self.config.get("default_model", "doubao-seedream-3-0-t2i-250415")
        image_size = self.action_data.get("size", self.config.get("default_size", "1024x1024"))

        # guidance_scale 现在完全由配置文件控制
        guidance_scale_input = self.config.get("default_guidance_scale", 2.5)  # 默认2.5
        guidance_scale_val = 2.5  # Fallback default
        try:
            guidance_scale_val = float(guidance_scale_input)
        except (ValueError, TypeError):
            logger.warning(
                f"{self.log_prefix} 配置文件中的 default_guidance_scale 值 '{guidance_scale_input}' 无效 (应为浮点数)，使用默认值 2.5。"
            )
            guidance_scale_val = 2.5

        # Seed parameter - ensure it's always an integer
        seed_config_value = self.config.get("default_seed")
        seed_val = 42  # Default seed if not configured or invalid
        if seed_config_value is not None:
            try:
                seed_val = int(seed_config_value)
            except (ValueError, TypeError):
                logger.warning(
                    f"{self.log_prefix} 配置文件中的 default_seed ('{seed_config_value}') 无效，将使用默认种子 42。"
                )
                # seed_val is already 42
        else:
            logger.info(
                f"{self.log_prefix} 未在配置中找到 default_seed，将使用默认种子 42。建议在配置文件中添加 default_seed。"
            )
            # seed_val is already 42

        # Watermark 现在完全由配置文件控制
        effective_watermark_source = self.config.get("default_watermark", True)  # 默认True
        if isinstance(effective_watermark_source, bool):
            watermark_val = effective_watermark_source
        elif isinstance(effective_watermark_source, str):
            watermark_val = effective_watermark_source.lower() == "true"
        else:
            logger.warning(
                f"{self.log_prefix} 配置文件中的 default_watermark 值 '{effective_watermark_source}' 无效 (应为布尔值或 'true'/'false')，使用默认值 True。"
            )
            watermark_val = True

        await self.send_message_by_expressor(
            f"收到！正在为您生成关于 '{description}' 的图片，请稍候...（模型: {default_model}, 尺寸: {image_size}）"
        )

        try:
            success, result = await asyncio.to_thread(
                self._make_http_image_request,
                prompt=description,
                model=default_model,
                size=image_size,
                seed=seed_val,
                guidance_scale=guidance_scale_val,
                watermark=watermark_val,
            )
        except Exception as e:
            logger.error(f"{self.log_prefix} (HTTP) 异步请求执行失败: {e!r}", exc_info=True)
            traceback.print_exc()
            success = False
            result = f"图片生成服务遇到意外问题: {str(e)[:100]}"

        if success:
            image_url = result
            logger.info(f"{self.log_prefix} 图片URL获取成功: {image_url[:70]}... 下载并编码.")

            try:
                encode_success, encode_result = await asyncio.to_thread(self._download_and_encode_base64, image_url)
            except Exception as e:
                logger.error(f"{self.log_prefix} (B64) 异步下载/编码失败: {e!r}", exc_info=True)
                traceback.print_exc()
                encode_success = False
                encode_result = f"图片下载或编码时发生内部错误: {str(e)[:100]}"

            if encode_success:
                base64_image_string = encode_result
                send_success = await self.send_message(type="image", data=base64_image_string)
                if send_success:
                    await self.send_message_by_expressor("图片表情已发送！")
                    return True, "图片表情已发送"
                else:
                    await self.send_message_by_expressor("图片已处理为Base64，但作为表情发送失败了。")
                    return False, "图片表情发送失败 (Base64)"
            else:
                await self.send_message_by_expressor(f"获取到图片URL，但在处理图片时失败了：{encode_result}")
                return False, f"图片处理失败(Base64): {encode_result}"
        else:
            error_message = result
            await self.send_message_by_expressor(f"哎呀，生成图片时遇到问题：{error_message}")
            return False, f"图片生成失败: {error_message}"

    def _download_and_encode_base64(self, image_url: str) -> Tuple[bool, str]:
        """下载图片并将其编码为Base64字符串"""
        logger.info(f"{self.log_prefix} (B64) 下载并编码图片: {image_url[:70]}...")
        try:
            with urllib.request.urlopen(image_url, timeout=30) as response:
                if response.status == 200:
                    image_bytes = response.read()
                    base64_encoded_image = base64.b64encode(image_bytes).decode("utf-8")
                    logger.info(f"{self.log_prefix} (B64) 图片下载编码完成. Base64长度: {len(base64_encoded_image)}")
                    return True, base64_encoded_image
                else:
                    error_msg = f"下载图片失败 (状态: {response.status})"
                    logger.error(f"{self.log_prefix} (B64) {error_msg} URL: {image_url}")
                    return False, error_msg
        except Exception as e:  # Catches all exceptions from urlopen, b64encode, etc.
            logger.error(f"{self.log_prefix} (B64) 下载或编码时错误: {e!r}", exc_info=True)
            traceback.print_exc()
            return False, f"下载或编码图片时发生错误: {str(e)[:100]}"

    def _make_http_image_request(
        self, prompt: str, model: str, size: str, seed: int | None, guidance_scale: float, watermark: bool
    ) -> Tuple[bool, str]:
        base_url = self.config.get("base_url")
        generate_api_key = self.config.get("volcano_generate_api_key")

        endpoint = f"{base_url.rstrip('/')}/images/generations"

        payload_dict = {
            "model": model,
            "prompt": prompt,
            "response_format": "url",
            "size": size,
            "guidance_scale": guidance_scale,
            "watermark": watermark,
            "seed": seed,  # seed is now always an int from process()
            "api-key": generate_api_key,
        }
        # if seed is not None: # No longer needed, seed is always an int
        #     payload_dict["seed"] = seed

        data = json.dumps(payload_dict).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {generate_api_key}",
        }

        logger.info(f"{self.log_prefix} (HTTP) 发起图片请求: {model}, Prompt: {prompt[:30]}... To: {endpoint}")
        logger.debug(
            f"{self.log_prefix} (HTTP) Request Headers: {{...Authorization: Bearer {generate_api_key[:10]}...}}"
        )
        logger.debug(
            f"{self.log_prefix} (HTTP) Request Body (api-key omitted): {json.dumps({k: v for k, v in payload_dict.items() if k != 'api-key'})}"
        )

        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                response_status = response.status
                response_body_bytes = response.read()
                response_body_str = response_body_bytes.decode("utf-8")

                logger.info(f"{self.log_prefix} (HTTP) 响应: {response_status}. Preview: {response_body_str[:150]}...")

                if 200 <= response_status < 300:
                    response_data = json.loads(response_body_str)
                    image_url = None
                    if (
                        isinstance(response_data.get("data"), list)
                        and response_data["data"]
                        and isinstance(response_data["data"][0], dict)
                    ):
                        image_url = response_data["data"][0].get("url")
                    elif response_data.get("url"):
                        image_url = response_data.get("url")

                    if image_url:
                        logger.info(f"{self.log_prefix} (HTTP) 图片生成成功，URL: {image_url[:70]}...")
                        return True, image_url
                    else:
                        logger.error(
                            f"{self.log_prefix} (HTTP) API成功但无图片URL. 响应预览: {response_body_str[:300]}..."
                        )
                        return False, "图片生成API响应成功但未找到图片URL"
                else:
                    logger.error(
                        f"{self.log_prefix} (HTTP) API请求失败. 状态: {response.status}. 正文: {response_body_str[:300]}..."
                    )
                    return False, f"图片API请求失败(状态码 {response.status})"
        except Exception as e:
            logger.error(f"{self.log_prefix} (HTTP) 图片生成时意外错误: {e!r}", exc_info=True)
            traceback.print_exc()
            return False, f"图片生成HTTP请求时发生意外错误: {str(e)[:100]}"
