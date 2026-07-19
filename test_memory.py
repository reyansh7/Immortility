import logging
from memory.experience_memory import ExperienceMemory

logging.basicConfig(level=logging.INFO)

def run_test():
    print("Initializing ExperienceMemory...")
    memory = ExperienceMemory(db_path=".immortility/test_experience.json")
    
    print("Adding fake bug fix...")
    memory.record_bug_fix(
        error_message="Test Error: Connection reset by peer",
        file_path="src/network.py",
        fix_applied="Added retry logic with backoff."
    )
    
    print(f"Experiences stored: {len(memory._cache)}")
    
    print("Clearing memory...")
    memory.clear_memory()
    
    print(f"Experiences stored after clear: {len(memory._cache)}")
    print("Test finished.")

if __name__ == "__main__":
    run_test()
