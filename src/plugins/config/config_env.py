import os
from pathlib import Path
from dotenv import load_dotenv

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
        self.ROOT_DIR = Path(__file__).parent.parent.parent.parent
        self.load_env()
    
    def load_env(self):
        env_file = self.ROOT_DIR / '.env'
        if env_file.exists():
            load_dotenv(env_file)
            
            # 根据ENVIRONMENT变量加载对应的环境文件
            env_type = os.getenv('ENVIRONMENT', 'prod')
            if env_type == 'dev':
                env_file = self.ROOT_DIR / '.env.dev'
            elif env_type == 'prod':
                env_file = self.ROOT_DIR / '.env.prod'
                
            if env_file.exists():
                load_dotenv(env_file, override=True)
    
    def get(self, key, default=None):
        return os.getenv(key, default)
    
    def get_all(self):
        return dict(os.environ)
    
    def __getattr__(self, name):
        return self.get(name)

# 创建全局实例
env_config = EnvConfig()

# 导出环境变量
def get_env(key, default=None):
    return os.getenv(key, default)

# 导出所有环境变量
def get_all_env():
    return dict(os.environ)