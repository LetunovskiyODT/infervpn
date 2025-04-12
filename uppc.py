# server_updater.py
import os
import shutil
import subprocess
import time

WATCH_PATH = "/root/updater/bot_update.py"
TARGET_PATH = "/root/actual_bot/bot.py"
BOT_DIR = "/root/actual_bot"
BOT_PROCESS_NAME = "bot.py"

def stop_bot():
    os.system(f"pkill -f {BOT_PROCESS_NAME}")

def start_bot():
    subprocess.Popen(["python3", TARGET_PATH], cwd=BOT_DIR)

def update_bot():
    if os.path.exists(WATCH_PATH):
        print("Обновление найдено. Остановка бота...")
        stop_bot()
        print("Замена bot.py...")
        shutil.copyfile(WATCH_PATH, TARGET_PATH)
        os.remove(WATCH_PATH)
        print("Перезапуск...")
        start_bot()
        print("Обновление завершено.")

if __name__ == "__main__":
    while True:
        update_bot()
        time.sleep(10)
