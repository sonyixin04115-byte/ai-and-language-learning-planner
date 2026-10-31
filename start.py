"""Start the local Python API and independent browser frontend."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
FRONTEND_PORT = 5174
BACKEND_PORT = 8001
FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}"
HEALTH_URL = f"http://127.0.0.1:{BACKEND_PORT}/api/health"


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def frontend_ready():
    try:
        with urllib.request.urlopen(FRONTEND_URL, timeout=0.8) as response:
            html = response.read(262144).decode("utf-8")
            return response.status == 200 and "知序" in html and "app.js" in html
    except (OSError, ValueError, UnicodeError):
        return False


def backend_ready():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=0.8) as response:
            health = json.load(response)
            return (response.status == 200
                    and health.get("app") == "zhixu-planner"
                    and str(health.get("version")) == "2")
    except (OSError, ValueError, AttributeError):
        return False


def open_page(no_browser):
    if not no_browser:
        webbrowser.open(FRONTEND_URL)


def stop_children(children):
    # Only stop processes created by this invocation; never kill by port or name.
    for child in children:
        if child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def run(no_browser=False):
    occupied = [port for port in (BACKEND_PORT, FRONTEND_PORT) if port_in_use(port)]
    if occupied:
        if backend_ready() and frontend_ready():
            print(f"F 盘知序已在运行，打开：{FRONTEND_URL}", flush=True)
            print("本次未重复启动服务；原来启动服务的窗口仍需保持运行。", flush=True)
            open_page(no_browser)
            return 0
        print("暂时不能启动：现有服务不是完整的 F 盘新版，或端口被其他程序占用。", flush=True)
        print("占用端口：" + ", ".join(map(str, occupied)), flush=True)
        print("请在原来的 PyCharm 运行面板停止旧服务，或关闭原来的启动窗口，再运行 start.py。", flush=True)
        print("程序没有停止任何现有进程，也没有修改账号或任务数据。", flush=True)
        return 1

    children = []
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               STUDY_PORT=str(BACKEND_PORT), STUDY_ORIGIN=FRONTEND_URL)
    try:
        print("正在启动 F 盘知序，请稍候……", flush=True)
        children.append(subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "backend" / "server.py")],
            cwd=ROOT, env=env))
        children.append(subprocess.Popen(
            [sys.executable, "-u", "-m", "http.server", str(FRONTEND_PORT),
             "--bind", "127.0.0.1", "--directory", str(ROOT / "frontend")],
            cwd=ROOT, env=env))

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("服务提前退出。请查看上方错误；检查 Python 解释器和端口。")
            if backend_ready() and frontend_ready():
                break
            time.sleep(0.15)
        else:
            raise RuntimeError("等待服务启动超时。请查看上方错误后重新运行 start.py。")

        print(f"启动成功：{FRONTEND_URL}", flush=True)
        print("在浏览器登录后使用。每日任务由你自己添加、安排和勾选。", flush=True)
        print("保持本运行面板开启；在这里按 Ctrl+C 可停止本次服务。", flush=True)
        open_page(no_browser)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("其中一个服务已退出，本次服务将一并停止。请查看上方错误。")
    except KeyboardInterrupt:
        print("\n正在停止本次服务，已保存的数据会保留。", flush=True)
        return 0
    except (OSError, RuntimeError) as error:
        print(f"启动未完成：{error}", flush=True)
        return 1
    finally:
        stop_children(children)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="启动 F 盘知序每日计划系统")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    sys.exit(run(no_browser=parser.parse_args().no_browser))
