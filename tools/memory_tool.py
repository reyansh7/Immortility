import os

MEMORY_FILE = "memory/memory.txt"

def load_memory() -> str:
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    return "No memory found."

def update_memory(new_content: str) -> str:
    with open(MEMORY_FILE, 'a', encoding='utf-8') as f:
        f.write("\n" + new_content)
    return "Memory updated."
