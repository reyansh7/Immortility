import psutil

class SystemTool:
    """Tool for system monitoring and process management."""

    @staticmethod
    def get_system_status() -> dict:
        """Returns global CPU, Memory, and Disk usage."""
        try:
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            return {
                "status": "success",
                "cpu_percent": psutil.cpu_percent(interval=0.5),
                "memory_total_gb": round(mem.total / (1024**3), 2),
                "memory_used_gb": round(mem.used / (1024**3), 2),
                "memory_percent": mem.percent,
                "disk_total_gb": round(disk.total / (1024**3), 2),
                "disk_used_gb": round(disk.used / (1024**3), 2),
                "disk_percent": disk.percent
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def list_top_processes(sort_by: str = "memory", limit: int = 10) -> dict:
        """Lists top processes sorted by 'memory' or 'cpu'."""
        try:
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'memory_percent', 'cpu_percent']):
                try:
                    processes.append(proc.info)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            sort_key = 'memory_percent' if sort_by == 'memory' else 'cpu_percent'
            processes = sorted(processes, key=lambda p: p[sort_key] or 0, reverse=True)
            
            return {
                "status": "success",
                "processes": processes[:limit]
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def kill_process(pid: int) -> dict:
        """Forcibly terminates a process by its PID."""
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(timeout=3)
            return {"status": "success", "message": f"Terminated process {pid}"}
        except psutil.NoSuchProcess:
            return {"status": "error", "message": f"Process {pid} does not exist."}
        except psutil.AccessDenied:
            return {"status": "error", "message": f"Access denied to terminate process {pid}."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
