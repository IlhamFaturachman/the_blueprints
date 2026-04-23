import os
import gzip
import shutil
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def rotate_log(log_path, max_size_mb=50, backup_count=5):
    """
    Checks if the log file exceeds the max size. 
    If yes, compresses it to .gz and rotates backups.
    """
    if not os.path.exists(log_path):
        return

    file_size_mb = os.path.getsize(log_path) / (1024 * 1024)
    if file_size_mb < max_size_mb:
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rotated_path = f"{log_path}.{timestamp}"
    gzip_path = f"{rotated_path}.gz"

    # [FIX-M-T5-4a] Use copy-then-truncate instead of rename-then-create.
    # Rename creates a window where the log file doesn't exist, causing
    # concurrent writers to lose log lines. Copy-truncate keeps the file
    # open and accessible throughout the rotation.
    try:
        shutil.copy2(log_path, rotated_path)
    except OSError as e:
        logger.error(f"[ROTATOR] Error copying log file: {e}")
        return

    try:
        with open(log_path, 'w') as f:
            f.write(f"--- Log rotated at {datetime.now().isoformat()} ---\n")
    except OSError as e:
        logger.error(f"[ROTATOR] Error truncating log file: {e}")

    # 3. Compress the renamed log file
    try:
        with open(rotated_path, 'rb') as f_in:
            with gzip.open(gzip_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(rotated_path)
        logger.info(f"[ROTATOR] Log rotated: {log_path} -> {gzip_path}")
    except Exception as e:
        logger.error(f"[ROTATOR] Error compressing rotated log: {e}")
        return

    # 3. Retention management (Keep only N latest .gz files)
    # [FIX-M-T5-4b] Default to "." if dirname is empty (bare filename)
    folder = os.path.dirname(log_path) or "."
    base_name = os.path.basename(log_path)
    gz_files = [
        os.path.join(folder, f) for f in os.listdir(folder) 
        if f.startswith(base_name) and f.endswith(".gz")
    ]
    gz_files.sort(key=os.path.getmtime, reverse=True)

    if len(gz_files) > backup_count:
        for old_file in gz_files[backup_count:]:
            try:
                os.remove(old_file)
                logger.info(f"[ROTATOR] Removed old log backup: {old_file}")
            except Exception as e:
                logger.error(f"[ROTATOR] Error removing old log: {e}")

def run_rotator_thread(log_path, interval_seconds=3600):
    """Background loop for log rotation."""
    import threading
    def _loop():
        while True:
            rotate_log(log_path)
            time.sleep(interval_seconds)
    
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t
