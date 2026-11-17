"""Turning text into embeddings (lists of numbers whose closeness reflects closeness in
meaning), with a small open model run on the CPU.

The model is downloaded from Hugging Face once and kept in Windmill's worker cache, which
survives between runs. It runs with onnxruntime, so no deep-learning framework (PyTorch)
is needed. Each text becomes one vector of length 1, so the similarity of two texts is the
dot product of their vectors (their cosine similarity, from -1 to 1).

The supported models are BGE models (BAAI): a text's embedding is the model's output for
its first token ([CLS]). Texts longer than max_tokens are cut short.
"""

from __future__ import annotations

import os

MODELS = {
    # name: the ONNX file in the model's Hugging Face repository
    "BAAI/bge-small-en-v1.5": "onnx/model.onnx",  # 384 numbers per text, ~6 s per 100 texts
    "BAAI/bge-base-en-v1.5": "onnx/model.onnx",  # 768 numbers per text, about 3x slower
}
WINDMILL_CACHE = "/tmp/windmill/cache"  # a volume each worker keeps between runs


def model_cache() -> str | None:
    """Where to keep downloaded models: in Windmill, the workers' cache; elsewhere, the
    usual Hugging Face cache (None)."""
    if os.path.isdir(WINDMILL_CACHE):
        return os.path.join(WINDMILL_CACHE, "models", "huggingface")
    return None


class Encoder:
    """One embedding model, ready to use: Encoder("BAAI/bge-small-en-v1.5").encode(texts)."""

    def __init__(self, model: str, max_tokens: int = 256) -> None:
        if model not in MODELS:
            raise ValueError(f"Unknown model {model!r}: choose one of {', '.join(MODELS)}")
        import onnxruntime
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        from .settings import use_hugging_face_token

        use_hugging_face_token()
        cache = model_cache()
        weights = hf_hub_download(model, MODELS[model], cache_dir=cache)
        tokenizer = hf_hub_download(model, "tokenizer.json", cache_dir=cache)
        self.model = model
        self.session = onnxruntime.InferenceSession(weights, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(tokenizer)
        self.tokenizer.enable_truncation(max_tokens)
        self.tokenizer.no_padding()

    def encode(self, texts: list[str], batch_size: int = 32, progress=None):
        """The texts' embeddings: a numpy array, one row (of length 1) per text, in the same
        order. progress, if given, is called with the fraction done (0 to 1) as it goes."""
        import numpy as np

        encoded = self.tokenizer.encode_batch(texts)
        # Shortest first, so each batch is padded only to texts of about its own length.
        order = sorted(range(len(texts)), key=lambda i: len(encoded[i].ids))
        result = None
        for start in range(0, len(order), batch_size):
            batch = [encoded[i] for i in order[start : start + batch_size]]
            width = max(len(e.ids) for e in batch)
            ids = np.zeros((len(batch), width), dtype=np.int64)
            mask = np.zeros((len(batch), width), dtype=np.int64)
            for row, e in enumerate(batch):
                ids[row, : len(e.ids)] = e.ids
                mask[row, : len(e.ids)] = 1
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            first_token = self.session.run(None, feed)[0][:, 0]
            vectors = first_token / np.linalg.norm(first_token, axis=1, keepdims=True)
            if result is None:
                result = np.zeros((len(texts), vectors.shape[1]), dtype=np.float32)
            result[order[start : start + batch_size]] = vectors
            if progress:
                progress(min(1.0, (start + batch_size) / len(order)))
        if result is None:
            return np.zeros((0, 0), dtype=np.float32)
        return result
