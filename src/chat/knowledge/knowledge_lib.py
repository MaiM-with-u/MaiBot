from src.chat.knowledge.lpmmconfig import PG_NAMESPACE, global_config
from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.llm_client import LLMClient
from src.chat.knowledge.mem_active_manager import MemoryActiveManager
from src.chat.knowledge.qa_manager import QAManager
from src.chat.knowledge.kg_manager import KGManager
from src.chat.knowledge.global_logger import logger
from src.config.config import global_config as bot_global_config
# try:
#     import quick_algo
# except ImportError:
#     print("quick_algo not found, please install it first")

# 检查LPMM知识库是否启用
if bot_global_config.lpmm_knowledge.enable:
    logger.info("正在初始化Mai-LPMM\n")
    logger.info("创建LLM客户端")
    llm_client_list = dict()
    for key in global_config["llm_providers"]:
        llm_client_list[key] = LLMClient(
            global_config["llm_providers"][key]["base_url"],
            global_config["llm_providers"][key]["api_key"],
        )

    # 初始化Embedding库
    embed_manager = EmbeddingManager(llm_client_list[global_config["embedding"]["provider"]])
    logger.info("正在从文件加载Embedding库")
    try:
        embed_manager.load_from_file()
    except Exception as e:
        logger.warning("此消息不会影响正常使用：从文件加载Embedding库时，{}".format(e))
        # logger.warning("如果你是第一次导入知识，或者还未导入知识，请忽略此错误")
    logger.info("Embedding库加载完成")
    # 初始化KG
    kg_manager = KGManager()
    logger.info("正在从文件加载KG")
    try:
        kg_manager.load_from_file()
    except Exception as e:
        logger.warning("此消息不会影响正常使用：从文件加载KG时，{}".format(e))
        # logger.warning("如果你是第一次导入知识，或者还未导入知识，请忽略此错误")
    logger.info("KG加载完成")

    logger.info(f"KG节点数量：{len(kg_manager.graph.get_node_list())}")
    logger.info(f"KG边数量：{len(kg_manager.graph.get_edge_list())}")


    # 数据比对：Embedding库与KG的段落hash集合
    for pg_hash in kg_manager.stored_paragraph_hashes:
        key = PG_NAMESPACE + "-" + pg_hash
        if key not in embed_manager.stored_pg_hashes:
            logger.warning(f"KG中存在Embedding库中不存在的段落：{key}")

    # 问答系统（用于知识库）
    qa_manager = QAManager(
        embed_manager,
        kg_manager,
        llm_client_list[global_config["embedding"]["provider"]],
        llm_client_list[global_config["qa"]["llm"]["provider"]],
        llm_client_list[global_config["qa"]["llm"]["provider"]],
    )

    # 记忆激活（用于记忆库）
    inspire_manager = MemoryActiveManager(
        embed_manager,
        llm_client_list[global_config["embedding"]["provider"]],
    )
else:
    logger.info("LPMM知识库已禁用，跳过初始化")
    # 创建空的占位符对象，避免导入错误
    qa_manager = None
    inspire_manager = None
