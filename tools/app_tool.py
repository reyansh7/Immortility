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
        """Open an application by name or executable/folder path."""
        target_name = name.strip().lower()
        target_path = path.strip()
        if not target_name and not target_path:
            return {"status": "error", "message": "No application name or path provided"}

        try:
            # Open a folder in VS Code or Explorer — never shell-exec a directory
            for candidate in (target_path, name.strip()):
                if candidate and os.path.isdir(candidate):
                    code_bin = shutil.which("code")
                    if code_bin:
                        if os.name == "nt":
                            subprocess.Popen(f'code "{candidate}"', shell=True)
                        else:
                            subprocess.Popen([code_bin, candidate])
                        return {"status": "success", "message": f"Opened folder in VS Code: {candidate}"}
                    if os.name == "nt":
                        subprocess.Popen(["explorer", candidate])
                    else:
                        subprocess.Popen(["xdg-open", candidate])
                    return {"status": "success", "message": f"Opened folder in Explorer: {candidate}"}

            if target_path and os.path.isfile(target_path):
                subprocess.Popen([target_path])
                return {"status": "success", "message": f"Opened {target_path}"}
            if target_name and os.path.isfile(target_name):
                subprocess.Popen([target_name])
                return {"status": "success", "message": f"Opened {target_name}"}

            cmd = AppTool._APP_MAP.get(target_name) or AppTool._APP_MAP.get(target_path.lower()) or target_path or target_name
            if target_name == "chrome" or (isinstance(cmd, str) and cmd.lower().endswith("chrome.exe")):
                from tools.user_browser import find_chrome_exe, resolve_chrome_profile

                chrome = find_chrome_exe()
                if chrome:
                    profile = resolve_chrome_profile()
                    subprocess.Popen(
                        [str(chrome), f"--profile-directory={profile}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return {
                        "status": "success",
                        "message": f"Opened Chrome (profile '{profile}')",
                    }
            if os.path.isfile(cmd):
                subprocess.Popen([cmd])
            elif isinstance(cmd, str) and cmd.startswith("start "):
                subprocess.Popen(cmd, shell=True)
            elif shutil.which(cmd):
                # Windows .CMD shims (e.g. code.CMD) need shell=True
                if os.name == "nt" and str(shutil.which(cmd)).lower().endswith((".cmd", ".bat")):
                    subprocess.Popen(cmd if " " in str(cmd) else f"{cmd}", shell=True)
                else:
                    subprocess.Popen([cmd])
            else:
                # Last resort — refuse bare directory paths (they aren't commands)
                if os.path.isdir(str(cmd)):
                    return AppTool.open_application(path=str(cmd))
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
