import os
import subprocess
import shutil


class AppTool:
    """Tool for launching local applications."""

    # Common application launch commands on Windows
    _APP_MAP = {
        "vscode": "code",
        "code": "code",
        "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "brave": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        "notepad": "notepad",
        "explorer": "explorer",
        "terminal": "wt",
        "powershell": "powershell",
        "whatsapp": "start whatsapp:",
    }

    @staticmethod
    def open_application(name: str = "", path: str = "") -> dict:
        """Open an application by name or executable path."""
        target_name = name.strip().lower()
        target_path = path.strip()
        if not target_name and not target_path:
            return {"status": "error", "message": "No application name or path provided"}

        try:
            if target_path and os.path.isfile(target_path):
                subprocess.Popen([target_path])
                return {"status": "success", "message": f"Opened {target_path}"}
            if target_name and os.path.isfile(target_name):
                subprocess.Popen([target_name])
                return {"status": "success", "message": f"Opened {target_name}"}

            cmd = AppTool._APP_MAP.get(target_name) or AppTool._APP_MAP.get(target_path.lower()) or target_path or target_name
            if os.path.isfile(cmd):
                subprocess.Popen([cmd])
            elif shutil.which(cmd):
                subprocess.Popen(cmd, shell=True)
            else:
                subprocess.Popen(cmd, shell=True)

            return {"status": "success", "message": f"Opened {name or path}"}
        except FileNotFoundError:
            return {"status": "error", "message": f"Application not found: {name or path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def open_vscode() -> dict:
        return AppTool.open_application("vscode")

    @staticmethod
    def open_chrome() -> dict:
        return AppTool.open_application("chrome")
