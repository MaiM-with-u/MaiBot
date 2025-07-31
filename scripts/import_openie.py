import sys
import os
from time import sleep
from multiprocessing import Manager

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.chat.knowledge.embedding_store import EmbeddingManager
from src.chat.knowledge.open_ie import OpenIE
from src.chat.knowledge.kg_manager import KGManager
from src.common.logger import get_logger
from src.chat.knowledge.utils.hash import get_sha256
from src.manager.local_store_manager import local_storage
from dotenv import load_dotenv

ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPENIE_DIR = os.path.join(ROOT_PATH, "data", "openie")
logger = get_logger("OpenIE导入")
ENV_FILE = os.path.join(ROOT_PATH, ".env")

if os.path.exists(".env"):
    load_dotenv(".env", override=True)
    print("成功加载环境变量配置")
else:
    print("未找到.env文件，请确保程序所需的环境变量被正确设置")
    raise FileNotFoundError(".env 文件不存在，请创建并配置所需的环境变量")

env_mask = {key: os.getenv(key) for key in os.environ}
def scan_provider(env_config: dict):
    provider = {}
    env_config = dict(filter(lambda item: item[0] not in env_mask, env_config.items()))
    for key in env_config:
        if key.endswith("_BASE_URL") or key.endswith("_KEY"):
            provider_name = key.split("_", 1)[0]
            if provider_name not in provider:
                provider[provider_name] = {"url": None, "key": None}
            if key.endswith("_BASE_URL"):
                provider[provider_name]["url"] = env_config[key]
            elif key.endswith("_KEY"):
                provider[provider_name]["key"] = env_config[key]
    for provider_name, config in provider.items():
        if config["url"] is None or config["key"] is None:
            logger.error(f"provider 内容：{config}\nenv_config 内容：{env_config}")
            raise ValueError(f"请检查 '{provider_name}' 提供商配置是否丢失 BASE_URL 或 KEY 环境变量")

def ensure_openie_dir():
    if not os.path.exists(OPENIE_DIR):
        os.makedirs(OPENIE_DIR)
        logger.info(f"创建OpenIE数据目录：{OPENIE_DIR}")
    else:
        logger.info(f"OpenIE数据目录已存在：{OPENIE_DIR}")

def hash_deduplicate(
    raw_paragraphs: dict[str, str],
    triple_list_data: dict[str, list[list[str]]],
    stored_pg_hashes: set,
    stored_paragraph_hashes: set,
):
    new_raw_paragraphs = {}
    new_triple_list_data = {}
    for _, (raw_paragraph, triple_list) in enumerate(
        zip(raw_paragraphs.values(), triple_list_data.values(), strict=False)
    ):
        paragraph_hash = get_sha256(raw_paragraph)
        if f"{local_storage['pg_namespace']}-{paragraph_hash}" in stored_pg_hashes and paragraph_hash in stored_paragraph_hashes:
            continue
        new_raw_paragraphs[paragraph_hash] = raw_paragraph
        new_triple_list_data[paragraph_hash] = triple_list
    return new_raw_paragraphs, new_triple_list_data

def handle_import_openie(openie_data: OpenIE, embed_manager: EmbeddingManager, kg_manager: KGManager, lock) -> bool:
    raw_paragraphs = openie_data.extract_raw_paragraph_dict()
    entity_list_data = openie_data.extract_entity_dict()
    triple_list_data = openie_data.extract_triple_dict()
    if len(raw_paragraphs) != len(entity_list_data) or len(raw_paragraphs) != len(triple_list_data):
        logger.error("OpenIE数据存在异常")
        logger.error(f"原始段落数量：{len(raw_paragraphs)}")
        logger.error(f"实体列表数量：{len(entity_list_data)}")
        logger.error(f"三元组列表数量：{len(triple_list_data)}")
        logger.error("OpenIE数据段落数量与实体列表数量或三元组列表数量不一致")
        logger.error("请保证你的原始数据分段良好，不要有类似于 “.....” 单独成一段的情况")
        logger.error("或者一段中只有符号的情况")
        logger.error("系统将于2秒后开始检查数据完整性")
        sleep(2)
        found_missing = False
        missing_idxs = []
        for doc in getattr(openie_data, "docs", []):
            idx = doc.get("idx", "<无idx>")
            passage = doc.get("passage", "<无passage>")
            missing = []
            if "passage" not in doc or not doc.get("passage"):
                missing.append("passage")
            if "extracted_entities" not in doc or not isinstance(doc.get("extracted_entities"), list):
                missing.append("名词列表缺失")
            elif len(doc.get("extracted_entities", [])) == 0:
                missing.append("名词列表为空")
            if "extracted_triples" not in doc or not isinstance(doc.get("extracted_triples"), list):
                missing.append("主谓宾三元组缺失")
            elif len(doc.get("extracted_triples", [])) == 0:
                missing.append("主谓宾三元组为空")
            if missing:
                found_missing = True
                missing_idxs.append(idx)
                logger.error("\n")
                logger.error("数据缺失：")
                logger.error(f"对应哈希值：{idx}")
                logger.error(f"对应文段内容内容：{passage}")
                logger.error(f"非法原因：{', '.join(missing)}")
        if not found_missing:
            logger.info("所有数据均完整，没有发现缺失字段。")
            return False
        logger.info(f"\n检测到非法文段，共{len(missing_idxs)}条。")
        logger.info("\n是否删除所有非法文段后继续导入？(y/n): ", end="")
        user_choice = input().strip().lower()
        if user_choice != "y":
            logger.info("用户选择不删除非法文段，程序终止。")
            sys.exit(1)
        logger.info("正在删除非法文段并继续导入...")
        openie_data.docs = [
            doc for doc in getattr(openie_data, "docs", []) if doc.get("idx", "<无idx>") not in missing_idxs
        ]
        raw_paragraphs = openie_data.extract_raw_paragraph_dict()
        entity_list_data = openie_data.extract_entity_dict()
        triple_list_data = openie_data.extract_triple_dict()
    if len(raw_paragraphs) != len(entity_list_data) or len(raw_paragraphs) != len(triple_list_data):
        logger.error("删除非法文段后，数据仍不一致，程序终止。")
        sys.exit(1)
    logger.info("正在进行段落去重与重索引")
    raw_paragraphs, triple_list_data = hash_deduplicate(
        raw_paragraphs,
        triple_list_data,
        embed_manager.stored_pg_hashes,
        kg_manager.stored_paragraph_hashes,
    )
    if len(raw_paragraphs) != 0:
        logger.info(f"段落去重完成，剩余待处理的段落数量：{len(raw_paragraphs)}")
        logger.info("开始Embedding")
        embed_manager.store_new_data_set(raw_paragraphs, triple_list_data, lock)
        logger.info("正在重新构建向量索引")
        embed_manager.rebuild_faiss_index()
        logger.info("向量索引构建完成")
        embed_manager.save_to_file()
        logger.info("Embedding完成")
        logger.info("开始构建RAG")
        kg_manager.build_kg(triple_list_data, embed_manager) # <--- 最终修正
        kg_manager.save_to_file()
        logger.info("RAG构建完成")
    else:
        logger.info("无新段落需要处理")
    return True

def main():
    manager = Manager()
    lock = manager.Lock()
    env_config = {key: os.getenv(key) for key in os.environ}
    scan_provider(env_config)
    print("=== 重要操作确认 ===")
    print("OpenIE导入时会大量发送请求，可能会撞到请求速度上限，请注意选用的模型")
    print("同之前样例：在本地模型下，在70分钟内我们发送了约8万条请求，在网络允许下，速度会更快")
    print("推荐使用硅基流动的Pro/BAAI/bge-m3")
    print("每百万Token费用为0.7元")
    print("知识导入时，会消耗大量系统资源，建议在较好配置电脑上运行")
    print("同上样例，导入时10700K几乎跑满，14900HX占用80%，峰值内存占用约3G")
    confirm = input("确认继续执行？(y/n): ").strip().lower()
    if confirm != "y":
        logger.info("用户取消操作")
        print("操作已取消")
        sys.exit(1)
    print("\n" + "=" * 40 + "\n")
    ensure_openie_dir()
    logger.info("----开始导入openie数据----\n")
    logger.info("创建LLM客户端")
    
    embed_manager = EmbeddingManager(lock)
    logger.info("正在从文件加载Embedding库")
    try:
        embed_manager.load_from_file()
    except Exception as e:
        if "不存在" in str(e):
            logger.error("如果你是第一次导入知识，请忽略此错误")
        else:
            logger.error(f"从文件加载Embedding库时发生错误：{e}")
            if "嵌入模型与本地存储不一致" in str(e):
                sys.exit(1)
    logger.info("Embedding库加载完成")
    
    kg_manager = KGManager()
    logger.info("正在从文件加载KG")
    try:
        kg_manager.load_from_file()
    except Exception as e:
        logger.error(f"从文件加载KG时发生错误：{e}")
        logger.error("如果你是第一次导入知识，请忽略此错误")
    logger.info("KG加载完成")

    logger.info(f"KG节点数量：{len(kg_manager.graph.get_node_list())}")
    logger.info(f"KG边数量：{len(kg_manager.graph.get_edge_list())}")

    for pg_hash in kg_manager.stored_paragraph_hashes:
        key = f"{local_storage['pg_namespace']}-{pg_hash}"
        if key not in embed_manager.stored_pg_hashes:
            logger.warning(f"KG中存在Embedding库中不存在的段落：{key}")

    logger.info("正在导入OpenIE数据文件")
    try:
        openie_data = OpenIE.load()
    except Exception as e:
        logger.error(f"导入OpenIE数据文件时发生错误：{e}")
        return False
    
    if handle_import_openie(openie_data, embed_manager, kg_manager, lock) is False:
        logger.error("处理OpenIE数据时发生错误")
        return False
    return None

if __name__ == "__main__":
    main()