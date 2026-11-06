import hashlib

import numpy as np


class HashEmbedder:
    dimension = 8

    def embed_documents(self, texts):
        result = []
        for text in texts:
            seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
            rng = np.random.default_rng(seed)
            vector = rng.normal(size=self.dimension)
            result.append((vector / np.linalg.norm(vector)).tolist())
        return result

    def embed_query(self, text):
        return self.embed_documents([text])[0]
