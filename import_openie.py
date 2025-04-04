try:
    import src.plugins.knowledge.lib.quick_algo
except ImportError:
    print("未找到quick_algo库，无法使用quick_algo算法")
    print("请安装quick_algo库 - 在lib.quick_algo中，执行命令：python setup.py build_ext --inplace")


from typing import Dict, List

from src.plugins.knowledge.src.config import PG_NAMESPACE, global_config
from src.plugins.knowledge.src.embedding_store import EmbeddingManager
from src.plugins.knowledge.src.llm_client import LLMClient
from src.plugins.knowledge.src.open_ie import OpenIE
from src.plugins.knowledge.src.kg_manager import KGManager
from src.plugins.knowledge.src.global_logger import logger
from src.plugins.knowledge.src.utils.hash import get_sha256


def hash_deduplicate(
    raw_paragraphs: Dict[str, str],
    triple_list_data: Dict[str, List[List[str]]],
    stored_pg_hashes: set,
    stored_paragraph_hashes: set,
):
    """Hash去重

    Args:
        raw_paragraphs: 索引的段落原文
        triple_list_data: 索引的三元组列表
        stored_pg_hashes: 已存储的段落hash集合
        stored_paragraph_hashes: 已存储的段落hash集合

    Returns:
        new_raw_paragraphs: 去重后的段落
        new_triple_list_data: 去重后的三元组
    """
    # 保存去重后的段落
    new_raw_paragraphs = dict()
    # 保存去重后的三元组
    new_triple_list_data = dict()

    for _, (raw_paragraph, triple_list) in enumerate(
        zip(raw_paragraphs.values(), triple_list_data.values())
    ):
        # 段落hash
        paragraph_hash = get_sha256(raw_paragraph)
        if ((PG_NAMESPACE + "-" + paragraph_hash) in stored_pg_hashes) and (
            paragraph_hash in stored_paragraph_hashes
        ):
            continue
        new_raw_paragraphs[paragraph_hash] = raw_paragraph
        new_triple_list_data[paragraph_hash] = triple_list

    return new_raw_paragraphs, new_triple_list_data


def handle_import_openie(
    openie_data: OpenIE, embed_manager: EmbeddingManager, kg_manager: KGManager
) -> bool:
    # 从OpenIE数据中提取段落原文与三元组列表
    # 索引的段落原文
    raw_paragraphs = openie_data.extract_raw_paragraph_dict()
    # 索引的实体列表
    entity_list_data = openie_data.extract_entity_dict()
    # 索引的三元组列表
    triple_list_data = openie_data.extract_triple_dict()
    if len(raw_paragraphs) != len(entity_list_data) or len(raw_paragraphs) != len(
        triple_list_data
    ):
        logger.error("OpenIE数据存在异常")
        return False
    # 将索引换为对应段落的hash值
    logger.info("正在进行段落去重与重索引")
    raw_paragraphs, triple_list_data = hash_deduplicate(
        raw_paragraphs,
        triple_list_data,
        embed_manager.stored_pg_hashes,
        kg_manager.stored_paragraph_hashes,
    )
    if len(raw_paragraphs) != 0:
        # 获取嵌入并保存
        logger.info(f"段落去重完成，剩余待处理的段落数量：{len(raw_paragraphs)}")
        logger.info("开始Embedding")
        embed_manager.store_new_data_set(raw_paragraphs, triple_list_data)
        # Embedding-Faiss重索引
        logger.info("正在重新构建向量索引")
        embed_manager.rebuild_faiss_index()
        logger.info("向量索引构建完成")
        embed_manager.save_to_file()
        logger.info("Embedding完成")
        # 构建新段落的RAG
        logger.info("开始构建RAG")
        kg_manager.build_kg(triple_list_data, embed_manager)
        kg_manager.save_to_file()
        logger.info("RAG构建完成")
    else:
        logger.info("无新段落需要处理")
    return True


def main():
    logger.info("----开始导入openie数据----\n")

    logger.info("创建LLM客户端")
    llm_client_list = dict()
    for key in global_config["llm_providers"]:
        llm_client_list[key] = LLMClient(
            global_config["llm_providers"][key]["base_url"],
            global_config["llm_providers"][key]["api_key"],
        )

    # 初始化Embedding库
    embed_manager = embed_manager = EmbeddingManager(
        llm_client_list[global_config["embedding"]["provider"]]
    )
    logger.info("正在从文件加载Embedding库")
    try:
        embed_manager.load_from_file()
    except Exception as e:
        logger.error("从文件加载Embedding库时发生错误：{}".format(e))
    logger.info("Embedding库加载完成")
    # 初始化KG
    kg_manager = KGManager()
    logger.info("正在从文件加载KG")
    try:
        kg_manager.load_from_file()
    except Exception as e:
        logger.error("从文件加载KG时发生错误：{}".format(e))
    logger.info("KG加载完成")

    logger.info(f"KG节点数量：{len(kg_manager.graph.nodes)}")
    logger.info(f"KG边数量：{len(kg_manager.graph.edges)}")

    # 数据比对：Embedding库与KG的段落hash集合
    for pg_hash in kg_manager.stored_paragraph_hashes:
        key = PG_NAMESPACE + "-" + pg_hash
        if key not in embed_manager.stored_pg_hashes:
            logger.warning(f"KG中存在Embedding库中不存在的段落：{key}")

    logger.info("正在导入OpenIE数据文件")
    try:
        openie_data = OpenIE.load()
    except Exception as e:
        logger.error("导入OpenIE数据文件时发生错误：{}".format(e))
        return False
    if handle_import_openie(openie_data, embed_manager, kg_manager) is False:
        logger.error("处理OpenIE数据时发生错误")
        return False


if __name__ == "__main__":
    main()
