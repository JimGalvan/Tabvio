import hashlib


class Utils:
    @staticmethod
    def hash_string(string: str) -> str:
        return hashlib.sha256(string.encode()).hexdigest()