"""O1 stage 4, shared: BGE text embeddings with ONNX on the CPU.

Download model files through Hugging Face and reuse the Windmill worker cache
when available. Represent each text with the model's first-token (CLS) output,
truncated to ``max_tokens``. Unit normalisation makes the dot product equal to
cosine similarity; vector dimension depends on the chosen model.
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
    """Use the persistent Windmill model cache, or Hugging Face's default cache locally."""
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

        from ...settings import use_hugging_face_token

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
        """Return unit vectors in input order, with shape (number of texts, model dimension).

        Batch texts of similar token lengths to limit padding, then restore their
        original order. The optional progress callback receives a fraction from 0 to 1.
        An empty input returns an empty array with shape (0, 0)."""
        import numpy as np

        tokenized_texts = self.tokenizer.encode_batch(texts)
        # Shortest first, so each batch is padded only to texts of about its own length.
        length_order = sorted(range(len(texts)), key=lambda i: len(tokenized_texts[i].ids))
        embeddings = None
        for start in range(0, len(length_order), batch_size):
            token_batch = [tokenized_texts[i] for i in length_order[start : start + batch_size]]
            padded_length = max(len(tokens.ids) for tokens in token_batch)
            input_ids = np.zeros((len(token_batch), padded_length), dtype=np.int64)
            attention_mask = np.zeros((len(token_batch), padded_length), dtype=np.int64)
            for row, tokens in enumerate(token_batch):
                input_ids[row, : len(tokens.ids)] = tokens.ids
                attention_mask[row, : len(tokens.ids)] = 1
            model_inputs = {"input_ids": input_ids, "attention_mask": attention_mask}
            if "token_type_ids" in self.inputs:
                model_inputs["token_type_ids"] = np.zeros_like(input_ids)
            cls_embeddings = self.session.run(None, model_inputs)[0][:, 0]
            vectors = cls_embeddings / np.linalg.norm(cls_embeddings, axis=1, keepdims=True)
            if embeddings is None:
                embeddings = np.zeros((len(texts), vectors.shape[1]), dtype=np.float32)
            embeddings[length_order[start : start + batch_size]] = vectors
            if progress:
                progress(min(1.0, (start + batch_size) / len(length_order)))
        if embeddings is None:
            return np.zeros((0, 0), dtype=np.float32)
        return embeddings
