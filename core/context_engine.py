from knowledge.engine import KnowledgeEngine
from core.action_engine import is_research_request
from agents.research_agent import ResearchAgent
from skills.leetcode_skill import LeetCodeSkill

class ContextEngine:
    def __init__(self):
        self.knowledge_engine = KnowledgeEngine()

    async def get_context(self, user_input: str, is_research: bool = False):
        if is_research:
            return await self._get_research_context(user_input)
        return await self._get_general_context(user_input)

    async def _get_research_context(self, user_input: str):
        researcher = ResearchAgent()
        return await researcher.execute(user_input)

    async def _get_general_context(self, user_input: str):
        project_name = self.knowledge_engine.get_active_project_name()
        if project_name:
            return self.knowledge_engine.get_context(user_input)
        return ""

    def is_research_request(self, user_input: str):
        return is_research_request(user_input)

    def is_leetcode_request(self, user_input: str):
        return "leetcode" in user_input.lower()

    def get_knowledge_engine(self):
        return self.knowledge_engine

context_engine = ContextEngine()