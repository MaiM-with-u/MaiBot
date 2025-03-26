import requests
import time
import uuid
import platform
import os
import json
import threading
from src.common.logger import get_module_logger
from src.plugins.chat.config import global_config

logger = get_module_logger("remote")

# UUID文件路径
UUID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_uuid.json")


# 生成或获取客户端唯一ID
def get_unique_id():
    # 检查是否已经有保存的UUID
    if os.path.exists(UUID_FILE):
        try:
            with open(UUID_FILE, "r") as f:
                data = json.load(f)
                if "client_id" in data:
                    # print("从本地文件读取客户端ID")
                    return data["client_id"]
        except (json.JSONDecodeError, IOError) as e:
            print(f"读取UUID文件出错: {e}，将生成新的UUID")

    # 如果没有保存的UUID或读取出错，则生成新的
    client_id = generate_unique_id()

    # 保存UUID到文件
    try:
        with open(UUID_FILE, "w") as f:
            json.dump({"client_id": client_id}, f)
        logger.info("已保存新生成的客户端ID到本地文件")
    except IOError as e:
        logger.error(f"保存UUID时出错: {e}")

    return client_id


# 生成客户端唯一ID
def generate_unique_id():
    # 结合主机名、系统信息和随机UUID生成唯一ID
    system_info = platform.system()
    unique_id = f"{system_info}-{uuid.uuid4()}"
    return unique_id


def send_heartbeat(server_url, client_id):
    """向服务器发送心跳"""
    sys = platform.system()
    try:
        headers = {"Client-ID": client_id, "User-Agent": f"HeartbeatClient/{client_id[:8]}"}
        data = json.dumps(
            {"system": sys, "Version": global_config.MAI_VERSION},
        )
        response = requests.post(f"{server_url}/api/clients", headers=headers, data=data)

        if response.status_code == 201:
            data = response.json()
            logger.debug(f"心跳发送成功。服务器响应: {data}")
            return True
        else:
            logger.debug(f"心跳发送失败。状态码: {response.status_code}")
            return False

    except requests.RequestException as e:
        logger.debug(f"发送心跳时出错: {e}")
        return False


class HeartbeatThread(threading.Thread):
    """心跳线程类"""

    def __init__(self, server_url, interval):
        super().__init__(daemon=True)  # 设置为守护线程，主程序结束时自动结束
        self.server_url = server_url
        self.interval = interval
        self.client_id = get_unique_id()
        self.running = True
        self.stop_event = threading.Event()  # 添加事件对象用于可中断的等待
        self.last_heartbeat_time = 0  # 记录上次发送心跳的时间

    def run(self):
        """线程运行函数"""
        logger.debug(f"心跳线程已启动，客户端ID: {self.client_id}")

        while self.running:
            # 发送心跳
            if send_heartbeat(self.server_url, self.client_id):
                logger.info(f"{self.interval}秒后发送下一次心跳...")
            else:
                logger.info(f"{self.interval}秒后重试...")

            self.last_heartbeat_time = time.time()

            # 使用可中断的等待代替 sleep
            # 每秒检查一次是否应该停止或发送心跳
            remaining_wait = self.interval
            while remaining_wait > 0 and self.running:
                # 每次最多等待1秒，便于及时响应停止请求
                wait_time = min(1, remaining_wait)
                if self.stop_event.wait(wait_time):
                    break  # 如果事件被设置，立即退出等待
                remaining_wait -= wait_time

                # 检查是否由于外部原因导致间隔异常延长
                if time.time() - self.last_heartbeat_time >= self.interval * 1.5:
                    logger.warning("检测到心跳间隔异常延长，立即发送心跳")
                    break

    def stop(self):
        """停止线程"""
        self.running = False
        self.stop_event.set()  # 设置事件，中断等待
        logger.debug("心跳线程已收到停止信号")


def main():
    if global_config.remote_enable:
        """主函数，启动心跳线程"""
        # 配置
        SERVER_URL = "http://hyybuth.xyz:10058"
        HEARTBEAT_INTERVAL = 300  # 5分钟（秒）

        # 创建并启动心跳线程
        heartbeat_thread = HeartbeatThread(SERVER_URL, HEARTBEAT_INTERVAL)
        heartbeat_thread.start()

        return heartbeat_thread  # 返回线程对象，便于外部控制
