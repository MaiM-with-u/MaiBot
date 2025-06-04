import asyncio

import requests
import platform

# from loguru import logger
from src.common.logger_manager import get_logger
from src.config.config import global_config
from src.manager.async_task_manager import AsyncTask
from src.manager.local_store_manager import local_storage

logger = get_logger("remote")

TELEMETRY_SERVER_URL = "http://hyybuth.xyz:10058"
"""遥测服务地址"""


class TelemetryHeartBeatTask(AsyncTask):
    HEARTBEAT_INTERVAL = 300

    def __init__(self):
        super().__init__(task_name="Telemetry Heart Beat Task", run_interval=self.HEARTBEAT_INTERVAL)
        self.server_url = TELEMETRY_SERVER_URL
        """遥测服务地址"""

        self.client_uuid = local_storage["mmc_uuid"] if "mmc_uuid" in local_storage else None
        """客户端UUID"""

        self.info_dict = self._get_sys_info()
        """系统信息字典"""

    @staticmethod
    def _get_sys_info() -> dict[str, str]:
        """获取系统信息"""
        info_dict = {
            "os_type": "Unknown",
            "py_version": platform.python_version(),
            "mmc_version": global_config.MMC_VERSION,
        }

        match platform.system():
            case "Windows":
                info_dict["os_type"] = "Windows"
            case "Linux":
                info_dict["os_type"] = "Linux"
            case "Darwin":
                info_dict["os_type"] = "macOS"
            case _:
                info_dict["os_type"] = "Unknown"

        return info_dict

    async def _req_uuid(self) -> bool:
        """
        向服务端请求UUID（不应在已存在UUID的情况下调用，会覆盖原有的UUID）
        """

        if "deploy_time" not in local_storage:
            logger.error("本地存储中缺少部署时间，无法请求UUID")
            return False

        try_count: int = 0
        while True:
            # 如果不存在，则向服务端请求一个新的UUID（注册客户端）
            logger.info("正在向遥测服务端请求UUID...")

            try:
                response = requests.post(
                    f"{TELEMETRY_SERVER_URL}/stat/reg_client",
                    json={"deploy_time": local_storage["deploy_time"]},
                    timeout=5,  # 设置超时时间为5秒
                )
            except Exception as e:
                logger.warning(f"请求UUID出错，不过你还是可以正常使用麦麦: {e}")  # 可能是网络问题

            logger.debug(f"{TELEMETRY_SERVER_URL}/stat/reg_client")

            logger.debug(local_storage["deploy_time"])

            logger.debug(response)

            if response.status_code == 200:
                data = response.json()
                if client_id := data.get("mmc_uuid"):
                    # 将UUID存储到本地
                    local_storage["mmc_uuid"] = client_id
                    self.client_uuid = client_id
                    logger.info(f"成功获取UUID: {self.client_uuid}")
                    return True  # 成功获取UUID，返回True
                else:
                    logger.error("无效的服务端响应")
            else:
                logger.error(
                    f"请求UUID失败，不过你还是可以正常使用麦麦，状态码: {response.status_code}, 响应内容: {response.text}"
                )

            # 请求失败，重试次数+1
            try_count += 1
            if try_count > 3:
                # 如果超过3次仍然失败，则退出
                logger.error("获取UUID失败，请检查网络连接或服务端状态")
                return False
            else:
                # 如果可以重试，等待后继续（指数退避）
                logger.info(f"获取UUID失败，将于 {4**try_count} 秒后重试...")
                await asyncio.sleep(4**try_count)

    async def _send_heartbeat(self):
        """向服务器发送心跳"""
        headers = {
            "Client-UUID": self.client_uuid,
            "User-Agent": f"HeartbeatClient/{self.client_uuid[:8]}",
        }

        logger.debug(f"正在发送心跳到服务器: {self.server_url}")

        logger.debug(headers)

        try:
            response = requests.post(
                f"{self.server_url}/stat/client_heartbeat",
                headers=headers,
                json=self.info_dict,
                timeout=5,  # 设置超时时间为5秒
            )
        except Exception as e:
            logger.warning(f"（此错误不会影响正常使用）状态未发生: {e}")

        logger.debug(response)

        # 处理响应
        if 200 <= response.status_code < 300:
            # 成功
            logger.debug(f"心跳发送成功，状态码: {response.status_code}")
        elif response.status_code == 403:
            # 403 Forbidden
            logger.warning(
                "（此错误不会影响正常使用）心跳发送失败，403 Forbidden: 可能是UUID无效或未注册。"
                "处理措施：重置UUID，下次发送心跳时将尝试重新注册。"
            )
            self.client_uuid = None
            del local_storage["mmc_uuid"]  # 删除本地存储的UUID
        else:
            # 其他错误
            logger.warning(
                f"（此错误不会影响正常使用）状态未发送，状态码: {response.status_code}, 响应内容: {response.text}"
            )

    async def run(self):
        # 发送心跳
        if global_config.telemetry.enable:
            if self.client_uuid is None and not await self._req_uuid():
                logger.warning("获取UUID失败，跳过此次心跳")
                return

            await self._send_heartbeat()
