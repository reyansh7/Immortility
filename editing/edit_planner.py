import json
import logging
import re
from dataclasses import dataclass
from typing import List, Dict
from core.action_engine import agent_step

logger = logging.getLogger(__name__)

@dataclass
class EditPlan:
    goal: str
    files_to_read: List[str]
    files_to_edit: List[str]
    dependencies: List[str]
    risks: List[str]
    verification_strategy: str
    symbols_to_modify: List[str]
    plan_steps: List[str]


class EditPlanner:
    """
    Multi-file edit planner. Determines all affected files before editing begins.
    """
    
    def __init__(self):
        pass
        
    async def generate_plan(self, user_request: str, project_context: str = "") -> EditPlan:
        """
        Interacts with the LLM to read the project context and user request,
        and generates a detailed EditPlan.
        """
        logger.info(f"Generating edit plan for: {user_request}")
        
        prompt = f"""
You are the Edit Planner. 
Your ONLY job is to fulfill the User Request. The Project Context is provided ONLY as background information. Do not get distracted by the Project Context.

Project Context: 
{project_context}

CRITICAL USER REQUEST (MUST FOLLOW THIS EXACTLY): 
{user_request}

Respond ONLY with a JSON object matching this schema (you may use <think> tags before the JSON):
{{
    "goal": "Brief description of what will be achieved based on the user request",
    "files_to_read": ["path/to/existing_file.py"],
    "files_to_edit": ["path/to/target_file.py"],
    "dependencies": ["List any related files, APIs, or modules that might be affected"],
    "risks": ["List potential side-effects or breaking changes"],
    "verification_strategy": "How should we test that this works? (e.g. run pytest, check linting, manual UI test)",
    "plan_steps": [
        "Read path/to/existing_file.py to understand current logic.",
        "Modify function X in path/to/target_file.py to do Y.",
        "Create path/to/new_file.py with class Z."
    ]
}}
"""
        response_text = await agent_step("Edit Planner", prompt, context=project_context)
        
        try:
            from core.json_utils import parse_llm_json

            data = parse_llm_json(response_text)
            plan_steps = data.get("plan_steps") or data.get("steps", [])
            if plan_steps and isinstance(plan_steps[0], dict):
                formatted_steps = []
                for s in plan_steps:
                    desc = s.get("description")
                    if not desc:
                        action = s.get("action", "")
                        details = s.get("details", "")
                        if action or details:
                            desc = f"{action} {details}".strip()
                        else:
                            desc = str(s)
                    formatted_steps.append(desc)
                plan_steps = formatted_steps

            return EditPlan(
                goal=data.get("goal", user_request),
                files_to_read=data.get("files_to_read", []),
                files_to_edit=data.get("files_to_edit", []),
                dependencies=data.get("dependencies", []),
                risks=data.get("risks", []),
                verification_strategy=data.get("verification_strategy", "Manual review"),
                symbols_to_modify=data.get("symbols_to_modify", []),
                plan_steps=plan_steps,
            )
        except Exception as e:
            logger.error(f"Failed to parse EditPlan JSON: {e}")
            return EditPlan(
                goal=user_request,
                files_to_read=[],
                files_to_edit=[],
                dependencies=[],
                risks=[],
                verification_strategy="None",
                symbols_to_modify=[],
                plan_steps=[]
            )
