from dataclasses import dataclass
import json
import os
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import tqdm
import faiss

from .llm_client import LLMClient
from .lpmmconfig import ENT_NAMESPACE, PG_NAMESPACE, REL_NAMESPACE, global_config
from .utils.hash import get_sha256
from .global_logger import logger


@dataclass
class EmbeddingStoreItem:
    """嵌入库中的项"""

    def __init__(self, item_hash: str, embedding: List[float], content: str):
        self.hash = item_hash
        self.embedding = embedding
        self.str = content

    def to_dict(self) -> dict:
        """转为dict"""
        return {
            "hash": self.hash,
            "embedding": self.embedding,
            "str": self.str,
        }


class EmbeddingStore:
    def __init__(self, llm_client: LLMClient, namespace: str, dir_path: str):
        self.namespace = namespace
        self.llm_client = llm_client
        self.dir = dir_path
        self.embedding_file_path = dir_path + "/" + namespace + ".parquet"
        self.index_file_path = dir_path + "/" + namespace + ".index"
        self.idx2hash_file_path = dir_path + "/" + namespace + "_i2h.json"

        self.store = dict()

        self.faiss_index = None
        self.idx2hash = None

    def _get_embedding(self, s: str) -> List[float]:
        return self.llm_client.send_embedding_request(
            global_config["embedding"]["model"], s
        )

    def batch_insert_strs(self, strs: List[str]) -> None:
        """向库中存入字符串"""
        # 逐项处理
        for s in tqdm.tqdm(strs, desc="存入嵌入库", unit="items"):
            # 计算hash去重
            item_hash = self.namespace + "-" + get_sha256(s)
            if item_hash in self.store:
                continue

            # 获取embedding
            embedding = self._get_embedding(s)

            # 存入
            self.store[item_hash] = EmbeddingStoreItem(item_hash, embedding, s)

    def save_to_file(self) -> None:
        """保存到文件"""
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
            logger.info(
                f"正在保存{self.namespace}嵌入库的FaissIndex到文件{self.index_file_path}"
            )
            faiss.write_index(self.faiss_index, self.index_file_path)
            logger.info(f"{self.namespace}嵌入库的FaissIndex保存成功")
            logger.info(
                f"正在保存{self.namespace}嵌入库的idx2hash映射到文件{self.idx2hash_file_path}"
            )
            with open(self.idx2hash_file_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.idx2hash, ensure_ascii=False, indent=4))
            logger.info(f"{self.namespace}嵌入库的idx2hash映射保存成功")

    def load_from_file(self) -> None:
        """从文件中加载"""
        if not os.path.exists(self.embedding_file_path):
            raise Exception(f"文件{self.embedding_file_path}不存在")

        logger.info(f"正在从文件{self.embedding_file_path}中加载{self.namespace}嵌入库")
        data_frame = pd.read_parquet(self.embedding_file_path, engine="pyarrow")
        for _, row in tqdm.tqdm(data_frame.iterrows(), total=len(data_frame)):
            self.store[row["hash"]] = EmbeddingStoreItem(
                row["hash"], row["embedding"], row["str"]
            )
        logger.info(f"{self.namespace}嵌入库加载成功")

        try:
            if os.path.exists(self.index_file_path):
                logger.info(
                    f"正在从文件{self.index_file_path}中加载{self.namespace}嵌入库的FaissIndex"
                )
                self.faiss_index = faiss.read_index(self.index_file_path)
                logger.info(f"{self.namespace}嵌入库的FaissIndex加载成功")
            else:
                raise Exception(f"文件{self.index_file_path}不存在")
            if os.path.exists(self.idx2hash_file_path):
                logger.info(
                    f"正在从文件{self.idx2hash_file_path}中加载{self.namespace}嵌入库的idx2hash映射"
                )
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
        """重新构建Faiss索引，以余弦相似度为度量"""
        # 获取所有的embedding
        array = []
        self.idx2hash = dict()
        for key in self.store:
            array.append(self.store[key].embedding)
            self.idx2hash[str(len(array) - 1)] = key
        embeddings = np.array(array, dtype=np.float32)
        # L2归一化
        faiss.normalize_L2(embeddings)
        # 构建索引
        self.faiss_index = faiss.IndexFlatIP(global_config["embedding"]["dimension"])
        self.faiss_index.add(embeddings)

    def search_top_k(self, query: List[float], k: int) -> List[Tuple[str, float]]:
        """搜索最相似的k个项，以余弦相似度为度量
        Args:
            query: 查询的embedding
            k: 返回的最相似的k个项
        Returns:
            result: 最相似的k个项的(hash, 余弦相似度)列表
        """
        if self.faiss_index is None:
            raise Exception("Faiss索引尚未构建")
        if self.idx2hash is None:
            raise Exception("idx2hash映射尚未构建")

        # L2归一化
        faiss.normalize_L2(np.array([query], dtype=np.float32))
        # 搜索
        distances, indices = self.faiss_index.search(np.array([query]), k)
        # 整理结果
        indices = list(indices.flatten())
        distances = list(distances.flatten())
        result = [
            (self.idx2hash[str(int(idx))], float(sim))
            for (idx, sim) in zip(indices, distances)
            if idx in range(len(self.idx2hash))
        ]

        return result


class EmbeddingManager:
    def __init__(self, llm_client: LLMClient):
        self.paragraphs_embedding_store = EmbeddingStore(
            llm_client,
            PG_NAMESPACE,
            global_config["persistence"]["embedding_data_dir"],
        )
        self.entities_embedding_store = EmbeddingStore(
            llm_client,
            ENT_NAMESPACE,
            global_config["persistence"]["embedding_data_dir"],
        )
        self.relation_embedding_store = EmbeddingStore(
            llm_client,
            REL_NAMESPACE,
            global_config["persistence"]["embedding_data_dir"],
        )
        self.stored_pg_hashes = set()

    def _store_pg_into_embedding(self, raw_paragraphs: Dict[str, str]):
        """将段落编码存入Embedding库"""
        self.paragraphs_embedding_store.batch_insert_strs(list(raw_paragraphs.values()))

    def _store_ent_into_embedding(self, triple_list_data: Dict[str, List[List[str]]]):
        """将实体编码存入Embedding库"""
        entities = set()
        for triple_list in triple_list_data.values():
            for triple in triple_list:
                entities.add(triple[0])
                entities.add(triple[2])
        self.entities_embedding_store.batch_insert_strs(list(entities))

    def _store_rel_into_embedding(self, triple_list_data: Dict[str, List[List[str]]]):
        """将关系编码存入Embedding库"""
        graph_triples = []  # a list of unique relation triple (in tuple) from all chunks
        for triples in triple_list_data.values():
            graph_triples.extend([tuple(t) for t in triples])
        graph_triples = list(set(graph_triples))
        self.relation_embedding_store.batch_insert_strs(
            [str(triple) for triple in graph_triples]
        )

    def load_from_file(self):
        """从文件加载"""
        self.paragraphs_embedding_store.load_from_file()
        self.entities_embedding_store.load_from_file()
        self.relation_embedding_store.load_from_file()
        # 从段落库中获取已存储的hash
        self.stored_pg_hashes = set(self.paragraphs_embedding_store.store.keys())

    def store_new_data_set(
        self,
        raw_paragraphs: Dict[str, str],
        triple_list_data: Dict[str, List[List[str]]],
    ):
        """存储新的数据集"""
        self._store_pg_into_embedding(raw_paragraphs)
        self._store_ent_into_embedding(triple_list_data)
        self._store_rel_into_embedding(triple_list_data)
        self.stored_pg_hashes.update(raw_paragraphs.keys())

    def save_to_file(self):
        """保存到文件"""
        self.paragraphs_embedding_store.save_to_file()
        self.entities_embedding_store.save_to_file()
        self.relation_embedding_store.save_to_file()

    def rebuild_faiss_index(self):
        """重建Faiss索引（请在添加新数据后调用）"""
        self.paragraphs_embedding_store.build_faiss_index()
        self.entities_embedding_store.build_faiss_index()
        self.relation_embedding_store.build_faiss_index()
