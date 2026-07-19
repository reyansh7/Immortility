import logging
from typing import Callable

logger = logging.getLogger(__name__)


class ReflectionEngine:
    """Manages retry loop with rollback on verification failure (max 3 attempts)."""

    def __init__(self, max_retries: int = 3):
        self.max_retries = max_retries

    async def execute_with_reflection(
        self, edit_func: Callable, verify_func: Callable, rollback_func: Callable, regenerate_func: Callable | None = None
    ) -> bool:
        for attempt in range(1, self.max_retries + 1):
            logger.info("Edit attempt %d/%d", attempt, self.max_retries)

            if not edit_func():
                logger.error("Edit function failed on attempt %d", attempt)
                rollback_func()
                # If editing failed (e.g. malformed patch), we can try regenerating immediately
                if regenerate_func and attempt < self.max_retries:
                    if not await regenerate_func("Patch application failed. Please generate a valid patch."):
                        return False
                continue

            verification_result = verify_func()
            if verification_result == "PASS":
                logger.info("Verification passed on attempt %d", attempt)
                return True

            logger.warning("Verification failed on attempt %d, rolling back. Error: %s", attempt, verification_result)
            rollback_func()
            
            if regenerate_func and attempt < self.max_retries:
                logger.info("Regenerating patch based on verification error...")
                # Pass the verification error to the LLM to get a new patch
                if not await regenerate_func(verification_result):
                    logger.error("Regeneration function failed to produce a new patch.")
                    return False

        logger.error("Maximum retries (%d) reached", self.max_retries)
        return False

    async def execute_with_reflection_sync(
        self,
        edit_func: Callable[[], bool],
        verify_func: Callable[[], str],
        rollback_func: Callable[[], None],
        regenerate_func: Callable | None = None,
    ) -> bool:
        # Forward to execute_with_reflection (now fully async aware)
        return await self.execute_with_reflection(edit_func, verify_func, rollback_func, regenerate_func)

