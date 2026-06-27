import os
import json
import logging
import numpy as np
from dotenv import load_dotenv
import google.generativeai as genai
from backend.extractor import get_gemini_model, get_openai_client


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(__file__), "db")
METADATA_FILE = os.path.join(DB_DIR, "logs_metadata.json")
EMBEDDINGS_FILE = os.path.join(DB_DIR, "logs_embeddings.npy")

# Ensure db directory exists
os.makedirs(DB_DIR, exist_ok=True)

def get_embedding(text: str) -> list:
    """
    Get embedding vector from Gemini or OpenAI.
    Returns a list of floats, or None on failure.
    """
    # 1. Try Gemini
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        try:
            genai.configure(api_key=api_key)
            result = genai.embed_content(
                model="models/gemini-embedding-001",
                content=text,
                task_type="retrieval_document"
            )
            return result['embedding']
        except Exception as e:
            logger.error(f"Gemini embedding API error: {e}")

    # 2. Try OpenAI
    openai_client = get_openai_client()
    if openai_client:
        try:
            response = openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=[text]
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"OpenAI embedding API error: {e}")

    return None

class LocalVectorStore:
    def __init__(self):
        self.metadata = []
        self.embeddings = []
        self.load()

    def load(self):
        """Load database from disk if it exists."""
        if os.path.exists(METADATA_FILE):
            try:
                with open(METADATA_FILE, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
            except Exception as e:
                logger.error(f"Error loading metadata: {e}")
                self.metadata = []

        if os.path.exists(EMBEDDINGS_FILE):
            try:
                arr = np.load(EMBEDDINGS_FILE)
                if arr.ndim == 2:
                    self.embeddings = arr.tolist()
                    logger.info(f"Loaded {len(self.embeddings)} embeddings with dimension {arr.shape[1]}")
                else:
                    logger.warning("Loaded embeddings have invalid dimensions, resetting embeddings.")
                    self.embeddings = []
            except Exception as e:
                logger.error(f"Error loading embeddings: {e}")
                self.embeddings = []

    def save(self):
        """Save database to disk."""
        try:
            with open(METADATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, indent=2, ensure_ascii=False)
            
            if self.embeddings:
                np.save(EMBEDDINGS_FILE, np.array(self.embeddings, dtype=np.float32))
        except Exception as e:
            logger.error(f"Error saving vector store: {e}")

    def add_logs(self, logs: list, platform: str, version: str = "", service: str = ""):
        """
        Embed and index a list of logs.
        logs: list of dicts with keys 'timestamp', 'severity', 'message', 'original_log'
        """
        new_embeddings = []
        new_metadata = []

        for log in logs:
            severity = log.get('severity') or log.get('level') or 'INFO'
            message = log.get('message') or log.get('original_log') or ''
            text_to_embed = f"[{platform}] {severity} {message}"
            vector = get_embedding(text_to_embed)
            
            if vector is not None:
                # Check if vector dimension matches existing embeddings
                if self.embeddings and len(vector) != len(self.embeddings[0]):
                    logger.warning(f"Embedding dimension mismatch ({len(vector)} vs {len(self.embeddings[0])}). Resetting existing embeddings database.")
                    self.embeddings = []
                    # Set previous metadata items to have has_embedding=False
                    for item in self.metadata:
                        item["has_embedding"] = False
                        
                # Also ensure it matches the new embeddings we are building in this batch
                if new_embeddings and len(vector) != len(new_embeddings[0]):
                    logger.warning("Dimension mismatch within the same batch. Skipping embedding.")
                    meta_item = log.copy()
                    meta_item.update({
                        "platform": platform,
                        "version": version,
                        "service": service,
                        "has_embedding": False
                    })
                    new_metadata.append(meta_item)
                    continue

                new_embeddings.append(vector)
                meta_item = log.copy()
                meta_item.update({
                    "platform": platform,
                    "version": version,
                    "service": service,
                    "has_embedding": True
                })
                new_metadata.append(meta_item)
            else:
                # Log without embedding (we will do keyword fallback for this)
                meta_item = log.copy()
                meta_item.update({
                    "platform": platform,
                    "version": version,
                    "service": service,
                    "has_embedding": False
                })
                new_metadata.append(meta_item)

        # Merge
        if new_embeddings:
            if len(self.embeddings) == 0:
                self.embeddings = new_embeddings
            else:
                self.embeddings.extend(new_embeddings)
                
        self.metadata.extend(new_metadata)
        self.save()
        logger.info(f"Indexed {len(logs)} log entries. Total in DB: {len(self.metadata)}")

    def search(self, query: str, limit: int = 10) -> list:
        """
        Search logs. Performs semantic search if embeddings are available,
        otherwise falls back to keyword matching.
        """
        if not self.metadata:
            return []

        query_vector = get_embedding(query)

        # If query vector is available and we have embedded logs, perform Cosine Similarity search
        embedded_indices = [i for i, meta in enumerate(self.metadata) if meta.get("has_embedding")]
        
        if query_vector is not None and len(embedded_indices) > 0 and len(self.embeddings) == len(embedded_indices):
            logger.info("Performing semantic search...")
            q_vec = np.array(query_vector, dtype=np.float32)
            # Normalize query vector
            q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-9)
            
            # Fetch embeddings
            db_vectors = np.array(self.embeddings, dtype=np.float32)
            # Normalize DB vectors
            norms = np.linalg.norm(db_vectors, axis=1, keepdims=True) + 1e-9
            db_vectors_normalized = db_vectors / norms
            
            # Compute cosine similarities
            similarities = np.dot(db_vectors_normalized, q_norm)
            
            # Rank indices
            ranked_sub_indices = np.argsort(similarities)[::-1]
            
            results = []
            for sub_idx in ranked_sub_indices[:limit]:
                original_idx = embedded_indices[sub_idx]
                score = float(similarities[sub_idx])
                
                res_item = self.metadata[original_idx].copy()
                res_item["score"] = score
                results.append(res_item)
                
            return results
        else:
            # Fallback: Simple keyword overlap matching
            logger.info("Performing keyword match fallback search...")
            query_words = set(query.lower().split())
            results = []
            
            for item in self.metadata:
                text = f"{item.get('platform', '')} {item.get('message', '')} {item.get('severity', '')}".lower()
                matches = sum(1 for word in query_words if word in text)
                if matches > 0:
                    score = matches / len(query_words)
                    res_item = item.copy()
                    res_item["score"] = score
                    results.append(res_item)
                    
            # Sort by keyword score
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:limit]

vector_store = LocalVectorStore()

if __name__ == "__main__":
    # Small test
    store = LocalVectorStore()
    test_logs = [
        {"timestamp": "2026-05-28", "severity": "ERROR", "message": "Failed to connect to postgresql database", "original_log": "error"},
        {"timestamp": "2026-05-28", "severity": "INFO", "message": "Successfully started server", "original_log": "info"}
    ]
    store.add_logs(test_logs, "TestPlat")
    print(store.search("database connection error"))
