#!/usr/bin/env python3
"""
TeraBox Unified Manager - GUI Edition for Linux Mint
Modern desktop application with CustomTkinter
"""

import os
import sys
import json
import time
import threading
from pathlib import Path
from typing import Optional, Dict, List

try:
    import customtkinter as ctk
except ImportError:
    print("Error: customtkinter not found. Run: pip install customtkinter")
    sys.exit(1)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("Error: watchdog not found. Run: pip install watchdog")
    sys.exit(1)

# Configure appearance
ctk.set_appearance_mode("System")  # "System", "Dark", "Light"
ctk.set_default_color_theme("blue")  # "blue", "green", "dark-blue"

CONFIG_DIR = Path.home() / ".config" / "terabox-manager"
CONFIG_FILE = CONFIG_DIR / "config.json"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.enc"
WATCH_FOLDERS_FILE = CONFIG_DIR / "watch_folders.json"


class TeraBoxAuth:
    """Handles authentication state."""

    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.session_cookies: Dict[str, str] = {}
        self.user_info: Dict = {}
        self._load_credentials()

    def _load_credentials(self):
        if CREDENTIALS_FILE.exists():
            try:
                with open(CREDENTIALS_FILE, 'r') as f:
                    data = json.load(f)
                    self.session_cookies = data.get('cookies', {})
                    self.user_info = data.get('user_info', {})
            except (json.JSONDecodeError, KeyError):
                pass

    def save_credentials(self, cookies: Dict[str, str], user_info: Dict):
        self.session_cookies = cookies
        self.user_info = user_info
        with open(CREDENTIALS_FILE, 'w') as f:
            json.dump({'cookies': cookies, 'user_info': user_info}, f, indent=2)
        os.chmod(CREDENTIALS_FILE, 0o600)

    def is_logged_in(self) -> bool:
        return bool(self.session_cookies)

    def logout(self):
        if CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()
        self.session_cookies = {}
        self.user_info = {}


class WatchFolderHandler(FileSystemEventHandler):
    """Handles filesystem events for auto-upload."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def on_created(self, event):
        if not event.is_directory:
            self.callback(event.src_path, "created")

    def on_modified(self, event):
        if not event.is_directory:
            self.callback(event.src_path, "modified")


class TeraBoxGUI(ctk.CTk):
    """Main GUI Application."""

    def __init__(self):
        super().__init__()

        self.title("TeraBox Manager")
        self.geometry("1000x700")
        self.minsize(800, 600)

        self.auth = TeraBoxAuth()
        self.observer = Observer()
        self.watch_handlers = {}
        self.current_remote_path = "/"

        # Grid layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(4, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar, text="📦 TeraBox\nManager", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 30))

        self.browse_btn = ctk.CTkButton(self.sidebar, text="Browse Files", command=self.show_browse_frame)
        self.browse_btn.grid(row=1, column=0, padx=20, pady=10)

        self.upload_btn = ctk.CTkButton(self.sidebar, text="Upload", command=self.show_upload_frame)
        self.upload_btn.grid(row=2, column=0, padx=20, pady=10)

        self.download_btn = ctk.CTkButton(self.sidebar, text="Download", command=self.show_download_frame)
        self.download_btn.grid(row=3, column=0, padx=20, pady=10)

        self.watch_btn = ctk.CTkButton(self.sidebar, text="Watch Folders", command=self.show_watch_frame)
        self.watch_btn.grid(row=4, column=0, padx=20, pady=10)

        self.status_label = ctk.CTkLabel(self.sidebar, text="● Not Logged In", text_color="red")
        self.status_label.grid(row=5, column=0, padx=20, pady=(10, 5))

        self.login_btn = ctk.CTkButton(self.sidebar, text="Login / Logout", command=self.toggle_login)
        self.login_btn.grid(row=6, column=0, padx=20, pady=(5, 20))

        # Main content area
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)

        # Initialize frames
        self.browse_frame = None
        self.upload_frame = None
        self.download_frame = None
        self.watch_frame = None
        self.login_frame = None

        # Show default frame
        self.show_browse_frame()
        self.update_status()

    def update_status(self):
        if self.auth.is_logged_in():
            self.status_label.configure(text="● Logged In", text_color="#2ecc71")
        else:
            self.status_label.configure(text="● Not Logged In", text_color="red")

    def clear_main_frame(self):
        for widget in self.main_frame.winfo_children():
            widget.destroy()

    def show_browse_frame(self):
        self.clear_main_frame()

        container = ctk.CTkFrame(self.main_frame)
        container.pack(fill="both", expand=True)

        header = ctk.CTkLabel(container, text="📂 Browse Remote Files", font=ctk.CTkFont(size=24, weight="bold"))
        header.pack(pady=(20, 10))

        path_frame = ctk.CTkFrame(container, fg_color="transparent")
        path_frame.pack(fill="x", padx=20, pady=10)

        self.path_entry = ctk.CTkEntry(path_frame, placeholder_text="Current path: /")
        self.path_entry.insert(0, self.current_remote_path)
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        go_btn = ctk.CTkButton(path_frame, text="Go", width=60, command=lambda: self.navigate_to(self.path_entry.get()))
        go_btn.pack(side="right")

        # File listbox simulation using scrollable frame
        self.file_list = ctk.CTkScrollableFrame(container, label_text="Files & Folders")
        self.file_list.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        # Load demo files
        self.refresh_file_list()

    def refresh_file_list(self):
        for widget in self.file_list.winfo_children():
            widget.destroy()

        if not self.auth.is_logged_in():
            lbl = ctk.CTkLabel(self.file_list, text="Please login to browse files", text_color="gray")
            lbl.pack(pady=40)
            return

        # Demo data - replace with real API/tbc call
        demo_files = [
            ("📁 Documents", "DIR"),
            ("📁 Videos", "DIR"),
            ("📁 Photos", "DIR"),
            ("📄 backup.tar.gz", "2.4 GB"),
            ("📄 notes.txt", "12 KB"),
            ("🎬 movie.mp4", "1.8 GB"),
        ]

        for name, size in demo_files:
            row = ctk.CTkFrame(self.file_list, fg_color="transparent")
            row.pack(fill="x", pady=2)

            icon_name = ctk.CTkLabel(row, text=name, anchor="w", font=ctk.CTkFont(size=14))
            icon_name.pack(side="left", padx=10)

            size_lbl = ctk.CTkLabel(row, text=size, anchor="e", text_color="gray")
            size_lbl.pack(side="right", padx=10)

    def navigate_to(self, path):
        self.current_remote_path = path
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, path)
        self.refresh_file_list()

    def show_upload_frame(self):
        self.clear_main_frame()

        container = ctk.CTkFrame(self.main_frame)
        container.pack(fill="both", expand=True)

        header = ctk.CTkLabel(container, text="⬆️ Upload Files", font=ctk.CTkFont(size=24, weight="bold"))
        header.pack(pady=(20, 10))

        # Drag drop zone simulation
        drop_zone = ctk.CTkFrame(container, height=200, border_width=2, border_color="gray")
        drop_zone.pack(fill="x", padx=40, pady=20)
        drop_zone.pack_propagate(False)

        dz_label = ctk.CTkLabel(drop_zone, text="Drag & Drop files here\nor click to select",
                                 font=ctk.CTkFont(size=16), text_color="gray")
        dz_label.place(relx=0.5, rely=0.5, anchor="center")

        select_btn = ctk.CTkButton(container, text="Select Files", command=self.select_upload_files)
        select_btn.pack(pady=10)

        self.upload_progress = ctk.CTkProgressBar(container, width=400)
        self.upload_progress.pack(pady=20)
        self.upload_progress.set(0)

        self.upload_status = ctk.CTkLabel(container, text="Ready to upload")
        self.upload_status.pack()

    def select_upload_files(self):
        if not self.auth.is_logged_in():
            self.upload_status.configure(text="❌ Please login first", text_color="red")
            return

        # Simulate upload
        self.upload_status.configure(text="Uploading...")
        self.simulate_upload()

    def simulate_upload(self):
        def do_upload():
            for i in range(101):
                time.sleep(0.03)
                self.after(0, lambda v=i: self.upload_progress.set(v / 100))
            self.after(0, lambda: self.upload_status.configure(text="✅ Upload complete!", text_color="#2ecc71"))

        threading.Thread(target=do_upload, daemon=True).start()

    def show_download_frame(self):
        self.clear_main_frame()

        container = ctk.CTkFrame(self.main_frame)
        container.pack(fill="both", expand=True)

        header = ctk.CTkLabel(container, text="⬇️ Download Files", font=ctk.CTkFont(size=24, weight="bold"))
        header.pack(pady=(20, 10))

        url_frame = ctk.CTkFrame(container, fg_color="transparent")
        url_frame.pack(fill="x", padx=40, pady=20)

        ctk.CTkLabel(url_frame, text="Remote Path or Share Link:").pack(anchor="w")
        self.dl_url_entry = ctk.CTkEntry(url_frame, placeholder_text="/Videos/movie.mp4 or https://...")
        self.dl_url_entry.pack(fill="x", pady=(5, 10))

        dest_frame = ctk.CTkFrame(container, fg_color="transparent")
        dest_frame.pack(fill="x", padx=40, pady=10)

        ctk.CTkLabel(dest_frame, text="Save to:").pack(anchor="w")
        self.dl_dest_entry = ctk.CTkEntry(dest_frame)
        self.dl_dest_entry.insert(0, str(Path.home() / "Downloads"))
        self.dl_dest_entry.pack(fill="x", pady=(5, 10))

        dl_btn = ctk.CTkButton(container, text="Start Download", command=self.start_download)
        dl_btn.pack(pady=20)

        self.dl_progress = ctk.CTkProgressBar(container, width=400)
        self.dl_progress.pack(pady=10)
        self.dl_progress.set(0)

        self.dl_status = ctk.CTkLabel(container, text="")
        self.dl_status.pack()

    def start_download(self):
        if not self.auth.is_logged_in():
            self.dl_status.configure(text="❌ Please login first", text_color="red")
            return

        self.dl_status.configure(text="Downloading...")

        def do_dl():
            for i in range(101):
                time.sleep(0.03)
                self.after(0, lambda v=i: self.dl_progress.set(v / 100))
            self.after(0, lambda: self.dl_status.configure(text="✅ Download complete!", text_color="#2ecc71"))

        threading.Thread(target=do_dl, daemon=True).start()

    def show_watch_frame(self):
        self.clear_main_frame()

        container = ctk.CTkFrame(self.main_frame)
        container.pack(fill="both", expand=True)

        header = ctk.CTkLabel(container, text="👁️ Watch Folders (Auto-Sync)", font=ctk.CTkFont(size=24, weight="bold"))
        header.pack(pady=(20, 10))

        add_frame = ctk.CTkFrame(container, fg_color="transparent")
        add_frame.pack(fill="x", padx=40, pady=20)

        self.watch_local_entry = ctk.CTkEntry(add_frame, placeholder_text="Local folder path")
        self.watch_local_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        add_btn = ctk.CTkButton(add_frame, text="Add Folder", width=100, command=self.add_watch_folder)
        add_btn.pack(side="right")

        self.watch_list = ctk.CTkScrollableFrame(container, label_text="Active Watch Folders")
        self.watch_list.pack(fill="both", expand=True, padx=40, pady=(0, 20))

        self.refresh_watch_list()

    def refresh_watch_list(self):
        for widget in self.watch_list.winfo_children():
            widget.destroy()

        if WATCH_FOLDERS_FILE.exists():
            try:
                with open(WATCH_FOLDERS_FILE, 'r') as f:
                    folders = json.load(f)
            except Exception:
                folders = []
        else:
            folders = []

        if not folders:
            lbl = ctk.CTkLabel(self.watch_list, text="No folders being watched", text_color="gray")
            lbl.pack(pady=20)
            return

        for folder in folders:
            row = ctk.CTkFrame(self.watch_list, fg_color="transparent")
            row.pack(fill="x", pady=5)

            info = ctk.CTkLabel(row, text=f"{folder['local']} → {folder['remote']}", anchor="w")
            info.pack(side="left", padx=10)

            rm_btn = ctk.CTkButton(row, text="Remove", width=70,
                                   command=lambda p=folder['local']: self.remove_watch_folder(p))
            rm_btn.pack(side="right", padx=10)

    def add_watch_folder(self):
        local_path = self.watch_local_entry.get().strip()
        if not local_path or not Path(local_path).exists():
            return

        remote_path = "/AutoUpload"
        folders = []
        if WATCH_FOLDERS_FILE.exists():
            try:
                with open(WATCH_FOLDERS_FILE, 'r') as f:
                    folders = json.load(f)
            except Exception:
                pass

        folders.append({
            'local': local_path,
            'remote': remote_path,
            'added': time.strftime('%Y-%m-%d %H:%M:%S')
        })

        with open(WATCH_FOLDERS_FILE, 'w') as f:
            json.dump(folders, f, indent=2)

        # Start watching
        handler = WatchFolderHandler(lambda path, evt: print(f"[Watch] {evt}: {path}"))
        self.observer.schedule(handler, local_path, recursive=True)
        self.watch_handlers[local_path] = handler

        if not self.observer.is_alive():
            self.observer.start()

        self.refresh_watch_list()
        self.watch_local_entry.delete(0, "end")

    def remove_watch_folder(self, local_path):
        folders = []
        if WATCH_FOLDERS_FILE.exists():
            try:
                with open(WATCH_FOLDERS_FILE, 'r') as f:
                    folders = json.load(f)
            except Exception:
                pass

        folders = [f for f in folders if f['local'] != local_path]
        with open(WATCH_FOLDERS_FILE, 'w') as f:
            json.dump(folders, f, indent=2)

        if local_path in self.watch_handlers:
            # Note: watchdog doesn't support unscheduling individual handlers easily
            # In production, restart observer or use a more sophisticated approach
            del self.watch_handlers[local_path]

        self.refresh_watch_list()

    def toggle_login(self):
        if self.auth.is_logged_in():
            self.auth.logout()
            self.update_status()
        else:
            self.show_login_dialog()

    def show_login_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Login to TeraBox")
        dialog.geometry("500x400")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="🔐 TeraBox Login",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(20, 10))

        ctk.CTkLabel(dialog, text="Extract cookies from browser DevTools\n(F12 → Application → Cookies)",
                     text_color="gray").pack(pady=(0, 20))

        ndus_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        ndus_frame.pack(fill="x", padx=40, pady=5)
        ctk.CTkLabel(ndus_frame, text="ndus cookie:").pack(anchor="w")
        ndus_entry = ctk.CTkEntry(ndus_frame)
        ndus_entry.pack(fill="x")

        panweb_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        panweb_frame.pack(fill="x", padx=40, pady=5)
        ctk.CTkLabel(panweb_frame, text="PANWEB cookie:").pack(anchor="w")
        panweb_entry = ctk.CTkEntry(panweb_frame)
        panweb_entry.insert(0, "1")
        panweb_entry.pack(fill="x")

        def do_login():
            ndus = ndus_entry.get().strip()
            panweb = panweb_entry.get().strip()
            if ndus:
                self.auth.save_credentials(
                    {'ndus': ndus, 'PANWEB': panweb},
                    {'username': 'user', 'vip_type': 0}
                )
                self.update_status()
                dialog.destroy()

        ctk.CTkButton(dialog, text="Login", command=do_login).pack(pady=30)

    def destroy(self):
        self.observer.stop()
        self.observer.join()
        super().destroy()


if __name__ == "__main__":
    app = TeraBoxGUI()
    app.mainloop()
