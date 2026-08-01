import hashlib
import os


def generate_file_id(filename: str) -> str:
    return hashlib.md5(filename.encode("utf-8")).hexdigest()


def get_index_path(filename: str):
    file_id = generate_file_id(filename)
    return os.path.join("app/uploads/vectors", file_id + ".index")


def get_chunks_path(filename: str):
    file_id = generate_file_id(filename)
    return os.path.join("app/uploads/vectors", file_id + ".pkl")