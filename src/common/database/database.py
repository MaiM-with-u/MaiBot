import os
from pymongo import MongoClient
from peewee import MySQLDatabase, SqliteDatabase
from pymongo.database import Database
from rich.traceback import install

from src.config.config import global_config

install(extra_lines=3)

_client = None
_db = None


def __create_database_instance():
    uri = os.getenv("MONGODB_URI")
    host = os.getenv("MONGODB_HOST", "127.0.0.1")
    port = int(os.getenv("MONGODB_PORT", "27017"))
    # db_name 变量在创建连接时不需要，在获取数据库实例时才使用
    username = os.getenv("MONGODB_USERNAME")
    password = os.getenv("MONGODB_PASSWORD")
    auth_source = os.getenv("MONGODB_AUTH_SOURCE")

    if uri:
        # 支持标准mongodb://和mongodb+srv://连接字符串
        if uri.startswith(("mongodb://", "mongodb+srv://")):
            return MongoClient(uri)
        else:
            raise ValueError(
                "Invalid MongoDB URI format. URI must start with 'mongodb://' or 'mongodb+srv://'. "
                "For MongoDB Atlas, use 'mongodb+srv://' format. "
                "See: https://www.mongodb.com/docs/manual/reference/connection-string/"
            )

    if username and password:
        # 如果有用户名和密码，使用认证连接
        return MongoClient(host, port, username=username, password=password, authSource=auth_source)

    # 否则使用无认证连接
    return MongoClient(host, port)


def get_db():
    """获取数据库连接实例，延迟初始化。"""
    global _client, _db
    if _client is None:
        _client = __create_database_instance()
        _db = _client[os.getenv("DATABASE_NAME", "MegBot")]
    return _db


class DBWrapper:
    """数据库代理类，保持接口兼容性同时实现懒加载。"""

    def __getattr__(self, name):
        return getattr(get_db(), name)

    def __getitem__(self, key):
        return get_db()[key]  # type: ignore


def create_peewee_database():
    data_base_config = global_config.data_base

    if data_base_config.db_type == "mysql":
        return MySQLDatabase(
            data_base_config.database,
            user=data_base_config.username,
            password=data_base_config.password,
            host=data_base_config.host,
            port=int(data_base_config.port),
            charset='utf8mb4'
        )
    elif data_base_config.db_type == "sqlite":
        ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        _DB_DIR = os.path.join(ROOT_PATH, "data")
        _DB_FILE = os.path.join(_DB_DIR, "MaiBot.db")
        os.makedirs(_DB_DIR, exist_ok=True)
        return SqliteDatabase(
            _DB_FILE,
            pragmas={
                "journal_mode": "wal",  # WAL模式提高并发性能
                "cache_size": -64 * 1000,  # 64MB缓存
                "foreign_keys": 1,
                "ignore_check_constraints": 0,
                "synchronous": 0,  # 异步写入提高性能
                "busy_timeout": 1000,  # 1秒超时而不是3秒
            }, )
    else:
        raise ValueError(f"Unsupported PEEWEE_DB_TYPE: {data_base_config.db_type}")


# 全局数据库访问点
memory_db: Database | DBWrapper = DBWrapper()

# 全局 Peewee SQLite 数据库访问点
db = create_peewee_database()
