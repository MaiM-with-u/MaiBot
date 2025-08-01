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
from multiprocessing import Manager

INVALID_ENTITY = [
    "", "你", "他", "她", "它", "我们", "你们", "他们", "她们", "它们",
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
    default_configs = {
        "root_path": ROOT_PATH,
        "data_path": f"{ROOT_PATH}/data",
        "lpmm_invalid_entity": INVALID_ENTITY,
        "pg_namespace": PG_NAMESPACE,
        "ent_namespace": ENT_NAMESPACE,
        "rel_namespace": REL_NAMESPACE,
        "rag_graph_namespace": RAG_GRAPH_NAMESPACE,
        "rag_ent_cnt_namespace": RAG_ENT_CNT_NAMESPACE,
        "rag_pg_hash_namespace": RAG_PG_HASH_NAMESPACE,
    }
    important_configs = {"root_path", "data_path"}
    initialized_count = 0
    for key, default_value in default_configs.items():
        if local_storage[key] is None:
            local_storage[key] = default_value
            if key in important_configs:
                logger.info(f"设置{key}: {default_value}")
            else:
                logger.debug(f"设置{key}: {default_value}")
            initialized_count += 1
    if initialized_count > 0:
        logger.info(f"知识库本地存储初始化完成，共设置 {initialized_count} 项配置")
    else:
        logger.debug("知识库本地存储配置已存在，跳过初始化")

_initialize_knowledge_local_storage()
qa_manager = None
inspire_manager = None

if bot_global_config.lpmm_knowledge.enable:
    logger.info("正在初始化Mai-LPMM")
    logger.info("创建LLM客户端")
    llm_client_list = {}
    for key in global_config["llm_providers"]:
        llm_client_list[key] = LLMClient(
            global_config["llm_providers"][key]["base_url"],
            global_config["llm_providers"][key]["api_key"],
        )
    
    manager = Manager()
    lock = manager.Lock()
    
    embed_manager = EmbeddingManager(lock)
    logger.info("正在从文件加载Embedding库")
    try:
        embed_manager.load_from_file()
    except Exception as e:
        logger.warning(f"此消息不会影响正常使用：从文件加载Embedding库时，{e}")
    logger.info("Embedding库加载完成")
    
    kg_manager = KGManager()
    logger.info("正在从文件加载KG")
    try:
        kg_manager.load_from_file()
    except Exception as e:
        logger.warning(f"此消息不会影响正常使用：从文件加载KG时，{e}")
    logger.info("KG加载完成")

    logger.info(f"KG节点数量：{len(kg_manager.graph.get_node_list())}")
    logger.info(f"KG边数量：{len(kg_manager.graph.get_edge_list())}")

    for pg_hash in kg_manager.stored_paragraph_hashes:
        key = f"{PG_NAMESPACE}-{pg_hash}"
        if key not in embed_manager.stored_pg_hashes:
            logger.warning(f"KG中存在Embedding库中不存在的段落：{key}")

    qa_manager = QAManager(embed_manager, kg_manager)
    inspire_manager = MemoryActiveManager(embed_manager, llm_client_list[global_config["embedding"]["provider"]])
else:
    logger.info("LPMM知识库已禁用，跳过初始化")