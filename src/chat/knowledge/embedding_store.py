from dataclasses import dataclass
import json
import os
import math
import asyncio
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import faiss
from .utils.hash import get_sha256
from .global_logger import logger
from rich.traceback import install
from rich.progress import (
    Progress,
    BarColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TaskProgressColumn,
    MofNCompleteColumn,
    SpinnerColumn,
    TextColumn,
)
from src.manager.local_store_manager import local_storage
from src.chat.utils.utils import get_embedding
from src.config.config import global_config

install(extra_lines=3)
ROOT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EMBEDDING_DATA_DIR = os.path.join(ROOT_PATH, "data", "embedding")
EMBEDDING_DATA_DIR_STR = str(EMBEDDING_DATA_DIR).replace("\\", "/")
TOTAL_EMBEDDING_TIMES = 3

EMBEDDING_TEST_STRINGS = [
    "阿卡伊真的太好玩了，神秘性感大女同等着你",
    "你怎么知道我arc12.64了",
    "我是蕾缪乐小姐的狗",
    "关注Oct谢谢喵",
    "不是w6我不草",
    "关注千石可乐谢谢喵",
    "来玩CLANNAD，AIR，樱之诗，樱之刻谢谢喵",
    "关注墨梓柒谢谢喵",
    "Ciallo~",
    "来玩巧克甜恋谢谢喵",
    "水印",
    "我也在纠结晚饭，铁锅炒鸡听着就香！",
    "test你妈喵",
]
EMBEDDING_TEST_FILE = os.path.join(ROOT_PATH, "data", "embedding_model_test.json")
EMBEDDING_SIM_THRESHOLD = 0.99

def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

@dataclass
class EmbeddingStoreItem:
    def __init__(self, item_hash: str, embedding: List[float], content: str):
        self.hash = item_hash
        self.embedding = embedding
        self.str = content

    def to_dict(self) -> dict:
        return {
            "hash": self.hash,
            "embedding": self.embedding,
            "str": self.str,
        }

class EmbeddingStore:
    def __init__(self, namespace: str, dir_path: str, lock):
        self.namespace = namespace
        self.dir = dir_path
        self.embedding_file_path = f"{dir_path}/{namespace}.parquet"
        self.index_file_path = f"{dir_path}/{namespace}.index"
        self.idx2hash_file_path = dir_path + "/" + namespace + "_i2h.json"
        self.store = {}
        self.faiss_index = None
        self.idx2hash = None
        self.lock = lock

    def _get_embedding(self, s: str) -> List[float]:
        with self.lock:
            try:
                asyncio.get_running_loop()
                import concurrent.futures
                def run_in_thread():
                    return asyncio.run(get_embedding(s))
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(run_in_thread)
                    result = future.result()
                    if result is None:
                        logger.error(f"获取嵌入失败: {s}")
                        return []
                    return result
            except RuntimeError:
                result = asyncio.run(get_embedding(s))
                if result is None:
                    logger.error(f"获取嵌入失败: {s}")
                    return []
                return result

    def get_test_file_path(self):
        return EMBEDDING_TEST_FILE

    def save_embedding_test_vectors(self):
        test_vectors = {}
        for idx, s in enumerate(EMBEDDING_TEST_STRINGS):
            test_vectors[str(idx)] = self._get_embedding(s)
        with open(self.get_test_file_path(), "w", encoding="utf-8") as f:
            json.dump(test_vectors, f, ensure_ascii=False, indent=2)

    def load_embedding_test_vectors(self):
        path = self.get_test_file_path()
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def check_embedding_model_consistency(self):
        local_vectors = self.load_embedding_test_vectors()
        if local_vectors is None:
            logger.warning("未检测到本地嵌入模型测试文件，将保存当前模型的测试嵌入。")
            self.save_embedding_test_vectors()
            return True
        for idx, s in enumerate(EMBEDDING_TEST_STRINGS):
            local_emb = local_vectors.get(str(idx))
            if local_emb is None:
                logger.warning("本地嵌入模型测试文件缺失部分测试字符串，将重新保存。")
                self.save_embedding_test_vectors()
                return True
            new_emb = self._get_embedding(s)
            sim = cosine_similarity(local_emb, new_emb)
            if sim < EMBEDDING_SIM_THRESHOLD:
                logger.error("嵌入模型一致性校验失败")
                return False
        logger.info("嵌入模型一致性校验通过。")
        return True

    def batch_insert_strs(self, strs: List[str], times: int) -> None:
        total = len(strs)
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(), MofNCompleteColumn(), "•", TimeElapsedColumn(), "<", TimeRemainingColumn(), transient=False) as progress:
            task = progress.add_task(f"存入嵌入库：({times}/{TOTAL_EMBEDDING_TIMES})", total=total)
            for s in strs:
                item_hash = self.namespace + "-" + get_sha256(s)
                if item_hash in self.store:
                    progress.update(task, advance=1)
                    continue
                embedding = self._get_embedding(s)
                self.store[item_hash] = EmbeddingStoreItem(item_hash, embedding, s)
                progress.update(task, advance=1)

    def save_to_file(self) -> None:
        data = []
        logger.info(f"正在保存{self.namespace}嵌入库到文件{self.embedding_file_path}")
        for item in self.store.values():
            data.append(item.to_dict())
        data_frame = pd.DataFrame(data)
        if not os.path.exists(self.dir):
            os.makedirs(self.dir, exist_ok=True)
        if not os.path.exists(self.embedding_file_path):
            open(self.embedding_file_path, "w").close()
        data_frame.to_parquet(self.embedding_file_path, engine="pyarrow", index=False)
        logger.info(f"{self.namespace}嵌入库保存成功")
        if self.faiss_index is not None and self.idx2hash is not None:
            logger.info(f"正在保存{self.namespace}嵌入库的FaissIndex到文件{self.index_file_path}")
            faiss.write_index(self.faiss_index, self.index_file_path)
            logger.info(f"{self.namespace}嵌入库的FaissIndex保存成功")
            logger.info(f"正在保存{self.namespace}嵌入库的idx2hash映射到文件{self.idx2hash_file_path}")
            with open(self.idx2hash_file_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.idx2hash, ensure_ascii=False, indent=4))
            logger.info(f"{self.namespace}嵌入库的idx2hash映射保存成功")

    def load_from_file(self) -> None:
        if not os.path.exists(self.embedding_file_path):
            raise Exception(f"文件{self.embedding_file_path}不存在")
        logger.info("正在加载嵌入库...")
        logger.debug(f"正在从文件{self.embedding_file_path}中加载{self.namespace}嵌入库")
        data_frame = pd.read_parquet(self.embedding_file_path, engine="pyarrow")
        total = len(data_frame)
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(), MofNCompleteColumn(), "•", TimeElapsedColumn(), "<", TimeRemainingColumn(), transient=False) as progress:
            task = progress.add_task("加载嵌入库", total=total)
            for _, row in data_frame.iterrows():
                self.store[row["hash"]] = EmbeddingStoreItem(row["hash"], row["embedding"], row["str"])
                progress.update(task, advance=1)
        logger.info(f"{self.namespace}嵌入库加载成功")
        try:
            if os.path.exists(self.index_file_path):
                logger.info(f"正在加载{self.namespace}嵌入库的FaissIndex...")
                logger.debug(f"正在从文件{self.index_file_path}中加载{self.namespace}嵌入库的FaissIndex")
                self.faiss_index = faiss.read_index(self.index_file_path)
                logger.info(f"{self.namespace}嵌入库的FaissIndex加载成功")
            else:
                raise Exception(f"文件{self.index_file_path}不存在")
            if os.path.exists(self.idx2hash_file_path):
                logger.info(f"正在加载{self.namespace}嵌入库的idx2hash映射...")
                logger.debug(f"正在从文件{self.idx2hash_file_path}中加载{self.namespace}嵌入库的idx2hash映射")
                with open(self.idx2hash_file_path, "r") as f:
                    self.idx2hash = json.load(f)
                logger.info(f"{self.namespace}嵌入库的idx2hash映射加载成功")
            else:
                raise Exception(f"文件{self.idx2hash_file_path}不存在")
        except Exception as e:
            logger.error(f"加载{self.namespace}嵌入库的FaissIndex时发生错误：{e}")
            logger.warning("正在重建Faiss索引")
            self.build_faiss_index()
            logger.info(f"{self.namespace}嵌入库的FaissIndex重建成功")
            self.save_to_file()

    def build_faiss_index(self) -> None:
        array = []
        self.idx2hash = dict()
        for key in self.store:
            array.append(self.store[key].embedding)
            self.idx2hash[str(len(array) - 1)] = key
        embeddings = np.array(array, dtype=np.float32)
        faiss.normalize_L2(embeddings)
        self.faiss_index = faiss.IndexFlatIP(global_config.lpmm_knowledge.embedding_dimension)
        self.faiss_index.add(embeddings)

    def search_top_k(self, query: List[float], k: int) -> List[Tuple[str, float]]:
        if self.faiss_index is None:
            logger.debug("FaissIndex尚未构建,返回None")
            return []
        if self.idx2hash is None:
            logger.warning("idx2hash尚未构建,返回None")
            return []
        faiss.normalize_L2(np.array([query], dtype=np.float32))
        distances, indices = self.faiss_index.search(np.array([query]), k)
        indices = list(indices.flatten())
        distances = list(distances.flatten())
        result = [
            (self.idx2hash[str(int(idx))], float(sim))
            for (idx, sim) in zip(indices, distances, strict=False)
            if idx in range(len(self.idx2hash))
        ]
        return result

class EmbeddingManager:
    def __init__(self, lock):
        self.lock = lock
        self.paragraphs_embedding_store = EmbeddingStore(
            local_storage["pg_namespace"],
            EMBEDDING_DATA_DIR_STR,
            self.lock,
        )
        self.entities_embedding_store = EmbeddingStore(
            local_storage["ent_namespace"],
            EMBEDDING_DATA_DIR_STR,
            self.lock,
        )
        self.relation_embedding_store = EmbeddingStore(
            local_storage["rel_namespace"],
            EMBEDDING_DATA_DIR_STR,
            self.lock,
        )
        self.stored_pg_hashes = set()

    def check_all_embedding_model_consistency(self):
        for store in [self.paragraphs_embedding_store, self.entities_embedding_store, self.relation_embedding_store]:
            if not store.check_embedding_model_consistency():
                return False
        return True

    def _store_pg_into_embedding(self, raw_paragraphs: Dict[str, str]):
        self.paragraphs_embedding_store.batch_insert_strs(list(raw_paragraphs.values()), times=1)

    def _store_ent_into_embedding(self, triple_list_data: Dict[str, List[List[str]]]):
        entities = set()
        for triple_list in triple_list_data.values():
            for triple in triple_list:
                entities.add(triple[0])
                entities.add(triple[2])
        self.entities_embedding_store.batch_insert_strs(list(entities), times=2)

    def _store_rel_into_embedding(self, triple_list_data: Dict[str, List[List[str]]]):
        graph_triples = []
        for triples in triple_list_data.values():
            graph_triples.extend([tuple(t) for t in triples])
        graph_triples = list(set(graph_triples))
        self.relation_embedding_store.batch_insert_strs([str(triple) for triple in graph_triples], times=3)

    def load_from_file(self):
        try:
            self.paragraphs_embedding_store.load_from_file()
        except Exception: pass
        try:
            self.entities_embedding_store.load_from_file()
        except Exception: pass
        try:
            self.relation_embedding_store.load_from_file()
        except Exception: pass
        self.stored_pg_hashes = set(self.paragraphs_embedding_store.store.keys())

    def store_new_data_set(
        self,
        raw_paragraphs: Dict[str, str],
        triple_list_data: Dict[str, List[List[str]]],
        lock
    ):
        if not self.check_all_embedding_model_consistency():
            raise Exception("嵌入模型与本地存储不一致，请检查模型设置或清空嵌入库后重试。")
        self._store_pg_into_embedding(raw_paragraphs)
        self._store_ent_into_embedding(triple_list_data)
        self._store_rel_into_embedding(triple_list_data)
        self.stored_pg_hashes.update(raw_paragraphs.keys())

    def save_to_file(self):
        self.paragraphs_embedding_store.save_to_file()
        self.entities_embedding_store.save_to_file()
        self.relation_embedding_store.save_to_file()

    def rebuild_faiss_index(self):
        self.paragraphs_embedding_store.build_faiss_index()
        self.entities_embedding_store.build_faiss_index()
        self.relation_embedding_store.build_faiss_index()