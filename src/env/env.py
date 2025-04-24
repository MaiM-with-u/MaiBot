import os
import shutil
from dotenv import load_dotenv

from common.common import BASE_PATH, TEMPLATE_PATH


class EnvConfig:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EnvConfig, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._load_env()

    def _init_env(self):
        # 检测.env文件是否存在
        env_file = BASE_PATH / ".env"
        if not env_file.exists():
            print("检测到.env文件不存在")
            TEMPLATE_ENV_PATH = TEMPLATE_PATH / "template.env"
            ENV_PATH = BASE_PATH / ".env"
            shutil.copy(TEMPLATE_ENV_PATH, ENV_PATH)
            print(f"已从{TEMPLATE_ENV_PATH}复制创建{ENV_PATH}，请修改配置后重新启动")

    def _load_env(self):
        self._init_env()
        env_file = BASE_PATH / ".env"
        load_dotenv(env_file)

        # 根据ENVIRONMENT变量加载对应的环境文件
        env_type = os.getenv("ENVIRONMENT", "prod")
        if env_type == "dev":
            env_file = BASE_PATH / ".env.dev"
        elif env_type == "prod":
            env_file = BASE_PATH / ".env"

        if env_file.exists():
            load_dotenv(env_file, override=True)

    def get(self, key, default=None):
        """获取环境变量"""
        return os.getenv(key, default)

    def getenv(self, key, default=None):
        """获取环境变量"""
        return self.get(key=key, default=default)

    def get_all(self):
        """获取所有环境变量"""
        return dict(os.environ)


# 创建全局实例
env = EnvConfig()
"""环境变量管理器"""
