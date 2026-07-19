from knowledge.engine import KnowledgeEngine
from knowledge.retrieval import RetrievalEngine

class ContextEngine:
    def __init__(self):
        self.knowledge_engine = KnowledgeEngine()
        self.retrieval_engine = RetrievalEngine()

    def get_context(self, query):
        return self.retrieval_engine.retrieve(query)
