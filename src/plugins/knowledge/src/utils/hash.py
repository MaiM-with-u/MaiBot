import hashlib


def get_sha256(string: str) -> str:
    """获取字符串的SHA256值"""
    sha256 = hashlib.sha256()
    sha256.update(string.encode("utf-8"))
    return sha256.hexdigest()
