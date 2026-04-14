#!/usr/bin/python3
"""
Log Rotation Script
Compresses old logs and removes ancient ones to prevent disk bloat.

Usage:
    python scripts/rotate_logs.py

Cron:
    0 0 * * 0 cd /home/jeff/Projects/alpaca && python scripts/rotate_logs.py
"""
import sys
import gzip
import shutil
from datetime import datetime, timedelta
from pathlib import Path


def ensure_log_dir():
    """Create logs directory if needed."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    return log_dir


def compress_old_logs(log_dir, days=7):
    """Compress log files older than specified days."""
    compressed = 0
    cutoff = datetime.now() - timedelta(days=days)
    
    for log_file in log_dir.glob("*.log"):
        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        
        if mtime < cutoff:
            gz_path = log_file.with_suffix(".log.gz")
            
            try:
                # Compress
                with open(log_file, "rb") as f_in:
                    with gzip.open(gz_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                
                # Remove original
                log_file.unlink()
                compressed += 1
                print(f"Compressed: {log_file.name} -> {gz_path.name}")
                
            except Exception as e:
                print(f"Error compressing {log_file}: {e}")
    
    return compressed


def delete_ancient_logs(log_dir, days=90):
    """Delete compressed logs older than specified days."""
    deleted = 0
    cutoff = datetime.now() - timedelta(days=days)
    
    for gz_file in log_dir.glob("*.log.gz"):
        mtime = datetime.fromtimestamp(gz_file.stat().st_mtime)
        
        if mtime < cutoff:
            try:
                gz_file.unlink()
                deleted += 1
                print(f"Deleted old: {gz_file.name}")
            except Exception as e:
                print(f"Error deleting {gz_file}: {e}")
    
    return deleted


def cleanup_reports(reports_dir, days=365):
    """Clean up old JSON reports."""
    if not reports_dir.exists():
        return 0
    
    deleted = 0
    cutoff = datetime.now() - timedelta(days=days)
    
    for report in reports_dir.glob("*.json"):
        mtime = datetime.fromtimestamp(report.stat().st_mtime)
        
        if mtime < cutoff:
            try:
                report.unlink()
                deleted += 1
                print(f"Deleted old report: {report.name}")
            except Exception as e:
                print(f"Error deleting {report}: {e}")
    
    return deleted


def main():
    try:
        print(f"[{datetime.now()}] Starting log rotation...")
        
        # Ensure directories exist
        log_dir = ensure_log_dir()
        reports_dir = Path("reports")
        
        # Rotate logs
        compressed = compress_old_logs(log_dir, days=7)
        deleted_logs = delete_ancient_logs(log_dir, days=90)
        deleted_reports = cleanup_reports(reports_dir, days=365)
        
        # Summary
        print(f"\nRotation complete:")
        print(f"  Compressed: {compressed} log files")
        print(f"  Deleted: {deleted_logs} old archives")
        print(f"  Deleted: {deleted_reports} old reports")
        
        # Show current disk usage
        total_size = sum(f.stat().st_size for f in log_dir.rglob("*") if f.is_file())
        total_size_mb = total_size / (1024 * 1024)
        print(f"  Current log dir size: {total_size_mb:.2f} MB")
        
        return 0
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
