from src.chat.knowledge.lpmmconfig import global_config
from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.llm_client import LLMClient
from src.chat.knowledge.mem_active_manager import MemoryActiveManager
from src.chat.knowledge.qa_manager import QAManager
from src.chat.knowledge.kg_manager import KGManager
from src.chat.knowledge.global_logger import logger
from src.config.config import global_config as bot_global_config
from src.manager.local_store_manager import local_storage
import os
from multiprocessing import Manager # <--- MODIFIED: 导入Manager

INVALID_ENTITY = [
    "",
    "你",
    "他",
    "她",
    "它",
    "我们",
    "你们",
    "他们",
    "她们",
    "它们",
]
PG_NAMESPACE = "paragraph"
ENT_NAMESPACE = "entity"
REL_NAMESPACE = "relation"

RAG_GRAPH_NAMESPACE = "rag-graph"
RAG_ENT_CNT_NAMESPACE = "rag-ent-cnt"
RAG_PG_HASH_NAMESPACE = "rag-pg-hash"


ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DATA_PATH = os.path.join(ROOT_PATH, "data")


def _initialize_knowledge_local_storage():
    """
    初始化知识库相关的本地存储配置
    使用字典批量设置，避免重复的if判断
    """
    # 定义所有需要初始化的配置项
    default_configs = {
        # 路径配置
        "root_path": ROOT_PATH,
        "data_path": f"{ROOT_PATH}/data",
        # 实体和命名空间配置
        "lpmm_invalid_entity": INVALID_ENTITY,
        "pg_namespace": PG_NAMESPACE,
        "ent_namespace": ENT_NAMESPACE,
        "rel_namespace": REL_NAMESPACE,
        # RAG相关命名空间配置
        "rag_graph_namespace": RAG_GRAPH_NAMESPACE,
        "rag_ent_cnt_namespace": RAG_ENT_CNT_NAMESPACE,
        "rag_pg_hash_namespace": RAG_PG_HASH_NAMESPACE,
    }

    # 日志级别映射：重要配置用info，其他用debug
    important_configs = {"root_path", "data_path"}

    # 批量设置配置项
    initialized_count = 0
    for key, default_value in default_configs.items():
        if local_storage[key] is None:
            local_storage[key] = default_value

            # 根据重要性选择日志级别
            if key in important_configs:
                logger.info(f"设置{key}: {default_value}")
            else:
                logger.debug(f"设置{key}: {default_value}")

            initialized_count += 1

    if initialized_count > 0:
        logger.info(f"知识库本地存储初始化完成，共设置 {initialized_count} 项配置")
    else:
        logger.debug("知识库本地存储配置已存在，跳过初始化")


# 初始化本地存储路径
# sourcery skip: dict-comprehension
_initialize_knowledge_local_storage()

qa_manager = None
inspire_manager = None

# 检查LPMM知识库是否启用
if bot_global_config.lpmm_knowledge.enable:
    logger.info("正在初始化Mai-LPMM")
    logger.info("创建LLM客户端")
    llm_client_list = {}
    for key in global_config["llm_providers"]:
        llm_client_list[key] = LLMClient(
            global_config["llm_providers"][key]["base_url"],  # type: ignore
            global_config["llm_providers"][key]["api_key"],  # type: ignore
        )

    # <--- MODIFIED: 创建Manager和Lock ---
    manager = Manager()
    lock = manager.Lock()
    # --->

    # 初始化Embedding库
    embed_manager = EmbeddingManager(lock) # <--- MODIFIED: 传递lock
    logger.info("正在从文件加载Embedding库")
    try:
        embed_manager.load_from_file()
    except Exception as e:
        logger.warning(f"此消息不会影响正常使用：从文件加载Embedding库时，{e}")
        # logger.warning("如果你是第一次导入知识，或者还未导入知识，请忽略此错误")
    logger.info("Embedding库加载完成")
    # 初始化KG
    kg_manager = KGManager()
    logger.info("正在从文件加载KG")
    try:
        kg_manager.load_from_file()
    except Exception as e:
        logger.warning(f"此消息不会影响正常使用：从文件加载KG时，{e}")
        # logger.warning("如果你是第一次导入知识，或者还未导入知识，请忽略此错误")
    logger.info("KG加载完成")

    logger.info(f"KG节点数量：{len(kg_manager.graph.get_node_list())}")
    logger.info(f"KG边数量：{len(kg_manager.graph.get_edge_list())}")

    # 数据比对：Embedding库与KG的段落hash集合
    for pg_hash in kg_manager.stored_paragraph_hashes:
        key = f"{PG_NAMESPACE}-{pg_hash}"
        if key not in embed_manager.stored_pg_hashes:
            logger.warning(f"KG中存在Embedding库中不存在的段落：{key}")

    # 问答系统（用于知识库）
    qa_manager = QAManager(
        embed_manager,
        kg_manager,
    )

    # 记忆激活（用于记忆库）
    inspire_manager = MemoryActiveManager(
        embed_manager,
        llm_client_list[global_config["embedding"]["provider"]],
    )
else:
    logger.info("LPMM知识库已禁用，跳过初始化")
    # 创建空的占位符对象，避免导入错误