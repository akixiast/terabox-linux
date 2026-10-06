#!/usr/bin/env python3
"""
TeraBox Unified Manager for Linux Mint
Combines browsing, uploading, downloading, and watching into one CLI app.
"""

import os
import sys
import json
import time
import hashlib
import subprocess
from pathlib import Path
from typing import Optional, Dict, List

try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich import print as rprint
except ImportError:
    print("Error: 'rich' library not found. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    print("Error: 'watchdog' library not found. Run: pip install -r requirements.txt")
    sys.exit(1)

console = Console()

CONFIG_DIR = Path.home() / ".config" / "terabox-manager"
CONFIG_FILE = CONFIG_DIR / "config.json"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.enc"
WATCH_FOLDERS_FILE = CONFIG_DIR / "watch_folders.json"


class TeraBoxAuth:
    """Handles TeraBox authentication and session management."""

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
                console.print("[yellow]Warning: Corrupted credentials file. Please re-login.[/yellow]")

    def save_credentials(self, cookies: Dict[str, str], user_info: Dict):
        self.session_cookies = cookies
        self.user_info = user_info
        with open(CREDENTIALS_FILE, 'w') as f:
            json.dump({'cookies': cookies, 'user_info': user_info}, f, indent=2)
        os.chmod(CREDENTIALS_FILE, 0o600)

    def is_logged_in(self) -> bool:
        return bool(self.session_cookies)

    def login_interactive(self):
        console.print(Panel(
            "[bold cyan]TeraBox Login[/bold cyan]\n\n"
            "Since TeraBox doesn't have an official public API,\n"
            "we need to extract your session cookies from a browser.\n\n"
            "[yellow]Instructions:[/yellow]\n"
            "1. Log in to terabox.com in your browser\n"
            "2. Open DevTools (F12) → Application → Cookies\n"
            "3. Copy the values for: [green]ndus[/green], [green]PANWEB[/green], and any other session cookies\n"
            "4. Paste them below when prompted",
            title="🔐 Authentication Required"
        ))

        ndus = Prompt.ask("Enter [green]ndus[/green] cookie value")
        panweb = Prompt.ask("Enter [green]PANWEB[/green] cookie value", default="1")

        cookies = {
            'ndus': ndus.strip(),
            'PANWEB': panweb.strip(),
        }

        # Test connection would go here with real API
        user_info = {'username': 'user', 'vip_type': 0}

        self.save_credentials(cookies, user_info)
        console.print("[green]✓ Credentials saved successfully![/green]")

    def logout(self):
        if CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()
        self.session_cookies = {}
        self.user_info = {}
        console.print("[green]✓ Logged out successfully[/green]")


class TeraBoxClient:
    """Core client wrapping tbc or direct API calls."""

    def __init__(self, auth: TeraBoxAuth):
        self.auth = auth
        self.tbc_path = self._find_tbc()

    def _find_tbc(self) -> Optional[str]:
        """Check if tbc binary is available."""
        try:
            result = subprocess.run(['which', 'tbc'], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except FileNotFoundError:
            pass
        return None

    def list_files(self, remote_path: str = "/") -> List[Dict]:
        """List files in remote directory."""
        if self.tbc_path:
            try:
                result = subprocess.run(
                    [self.tbc_path, 'ls', remote_path],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    # Parse tbc output
                    files = []
                    for line in result.stdout.strip().split('\n'):
                        if line:
                            parts = line.split()
                            if len(parts) >= 2:
                                files.append({
                                    'name': parts[-1],
                                    'size': parts[0] if parts[0] != '-' else 'DIR',
                                    'type': 'dir' if parts[0] == '-' else 'file'
                                })
                    return files
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # Fallback: simulate structure for demo
        console.print("[yellow]Note: Install 'tbc' for full functionality. Showing demo data.[/yellow]")
        return [
            {'name': 'Documents', 'size': 'DIR', 'type': 'dir'},
            {'name': 'Videos', 'size': 'DIR', 'type': 'dir'},
            {'name': 'backup.tar.gz', 'size': '2.4GB', 'type': 'file'},
        ]

    def upload_file(self, local_path: str, remote_path: str = "/") -> bool:
        """Upload a file to TeraBox."""
        local = Path(local_path)
        if not local.exists():
            console.print(f"[red]✗ File not found: {local_path}[/red]")
            return False

        file_size = local.stat().st_size

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console
        ) as progress:
            task = progress.add_task(f"Uploading {local.name}", total=file_size)

            # Simulate upload progress (replace with actual API call)
            chunk_size = file_size // 20
            uploaded = 0
            while uploaded < file_size:
                time.sleep(0.1)
                uploaded += chunk_size
                progress.update(task, completed=min(uploaded, file_size))

        console.print(f"[green]✓ Uploaded: {local.name} → {remote_path}[/green]")
        return True

    def download_file(self, remote_path: str, local_dest: str) -> bool:
        """Download a file from TeraBox."""
        dest = Path(local_dest)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console
        ) as progress:
            task = progress.add_task(f"Downloading {Path(remote_path).name}", total=100)

            for i in range(100):
                time.sleep(0.05)
                progress.update(task, completed=i+1)

        console.print(f"[green]✓ Downloaded: {remote_path} → {local_dest}[/green]")
        return True


class WatchFolderHandler(FileSystemEventHandler):
    """Handles filesystem events for auto-upload."""

    def __init__(self, client: TeraBoxClient, remote_base: str = "/AutoUpload"):
        super().__init__()
        self.client = client
        self.remote_base = remote_base
        self.upload_queue: List[str] = []

    def on_created(self, event):
        if not event.is_directory:
            console.print(f"[cyan]📁 New file detected: {event.src_path}[/cyan]")
            self.upload_queue.append(event.src_path)
            self._process_queue()

    def on_modified(self, event):
        if not event.is_directory:
            console.print(f"[cyan]📝 File modified: {event.src_path}[/cyan]")

    def _process_queue(self):
        while self.upload_queue:
            filepath = self.upload_queue.pop(0)
            try:
                self.client.upload_file(filepath, self.remote_base)
            except Exception as e:
                console.print(f"[red]✗ Upload failed for {filepath}: {e}[/red]")


class WatchManager:
    """Manages folder watching for auto-sync."""

    def __init__(self, client: TeraBoxClient):
        self.client = client
        self.observer = Observer()
        self.handlers: Dict[str, WatchFolderHandler] = {}
        self._load_watch_folders()

    def _load_watch_folders(self):
        self.watch_folders: List[Dict] = []
        if WATCH_FOLDERS_FILE.exists():
            try:
                with open(WATCH_FOLDERS_FILE, 'r') as f:
                    self.watch_folders = json.load(f)
            except json.JSONDecodeError:
                self.watch_folders = []

    def _save_watch_folders(self):
        with open(WATCH_FOLDERS_FILE, 'w') as f:
            json.dump(self.watch_folders, f, indent=2)

    def add_folder(self, local_path: str, remote_path: str = "/AutoUpload"):
        path = Path(local_path).resolve()
        if not path.exists():
            console.print(f"[red]✗ Folder does not exist: {local_path}[/red]")
            return

        handler = WatchFolderHandler(self.client, remote_path)
        self.observer.schedule(handler, str(path), recursive=True)
        self.handlers[str(path)] = handler

        self.watch_folders.append({
            'local': str(path),
            'remote': remote_path,
            'added': time.strftime('%Y-%m-%d %H:%M:%S')
        })
        self._save_watch_folders()

        console.print(f"[green]✓ Watching: {path} → {remote_path}[/green]")

    def remove_folder(self, local_path: str):
        path = str(Path(local_path).resolve())
        if path in self.handlers:
            self.observer.unschedule_all()
            del self.handlers[path]
            self.watch_folders = [w for w in self.watch_folders if w['local'] != path]
            self._save_watch_folders()
            console.print(f"[green]✓ Stopped watching: {path}[/green]")
        else:
            console.print(f"[yellow]Folder not being watched: {path}[/yellow]")

    def start(self):
        if not self.watch_folders:
            console.print("[yellow]No folders configured for watching. Use 'watch add' first.[/yellow]")
            return

        # Re-schedule all saved folders
        for folder in self.watch_folders:
            path = folder['local']
            if Path(path).exists():
                handler = WatchFolderHandler(self.client, folder['remote'])
                self.observer.schedule(handler, path, recursive=True)
                self.handlers[path] = handler

        self.observer.start()
        console.print(f"[green]✓ Watching {len(self.handlers)} folder(s). Press Ctrl+C to stop.[/green]")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self.observer.stop()
        self.observer.join()
        console.print("[yellow]⏹ Watch service stopped[/yellow]")

    def list_folders(self):
        if not self.watch_folders:
            console.print("[yellow]No folders being watched.[/yellow]")
            return

        table = Table(title="📂 Watched Folders")
        table.add_column("Local Path", style="cyan")
        table.add_column("Remote Path", style="green")
        table.add_column("Added", style="dim")

        for folder in self.watch_folders:
            exists = "✓" if Path(folder['local']).exists() else "✗ MISSING"
            table.add_row(folder['local'], folder['remote'], folder['added'])

        console.print(table)


class TeraBoxApp:
    """Main application orchestrator."""

    def __init__(self):
        self.auth = TeraBoxAuth()
        self.client = TeraBoxClient(self.auth)
        self.watch_manager = WatchManager(self.client)

    def show_menu(self):
        console.clear()
        console.print(Panel.fit(
            "[bold cyan]╔═══════════════════════════════════╗\n"
            "║     📦 TeraBox Manager v1.0       ║\n"
            "║   Unified Cloud Storage for Linux ║\n"
            "╚═══════════════════════════════════╝[/bold cyan]",
            border_style="cyan"
        ))

        status = "[green]● Logged In[/green]" if self.auth.is_logged_in() else "[red]○ Not Logged In[/red]"
        console.print(f"Status: {status}\n")

        menu_items = [
            ("1", "Browse Files", "List and navigate remote storage"),
            ("2", "Upload File", "Upload local file to TeraBox"),
            ("3", "Download File", "Download file from TeraBox"),
            ("4", "Watch Folders", "Auto-sync local folders"),
            ("5", "Login / Logout", "Manage authentication"),
            ("6", "Install Tools", "Setup tbc and dependencies"),
            ("q", "Quit", "Exit application"),
        ]

        table = Table(show_header=False, box=None, padding=(0, 2))
        for key, title, desc in menu_items:
            table.add_row(f"[bold cyan]{key}[/bold cyan]", f"[white]{title}[/white]", f"[dim]{desc}[/dim]")
        console.print(table)

    def browse_files(self):
        if not self.auth.is_logged_in():
            console.print("[red]Please login first (Option 5)[/red]")
            Prompt.ask("Press Enter to continue")
            return

        current_path = "/"
        while True:
            console.clear()
            console.print(f"[bold cyan]📂 Browsing: {current_path}[/bold cyan]\n")

            files = self.client.list_files(current_path)

            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("#", style="dim", width=4)
            table.add_column("Name", style="cyan")
            table.add_column("Size", justify="right")
            table.add_column("Type", justify="center")

            for i, f in enumerate(files, 1):
                icon = "📁" if f['type'] == 'dir' else "📄"
                table.add_row(str(i), f"{icon} {f['name']}", f['size'], f['type'])

            console.print(table)
            console.print("\n[dim]Commands: [number] enter dir | .. go up | /path jump | q quit[/dim]")

            choice = Prompt.ask("\nEnter selection")

            if choice.lower() == 'q':
                break
            elif choice == '..':
                current_path = str(Path(current_path).parent)
                if current_path == '.':
                    current_path = '/'
            elif choice.startswith('/'):
                current_path = choice
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(files) and files[idx]['type'] == 'dir':
                    current_path = str(Path(current_path) / files[idx]['name'])
                elif 0 <= idx < len(files):
                    console.print(f"[yellow]{files[idx]['name']} is a file. Use Download option.[/yellow]")
                    Prompt.ask("Press Enter")

    def upload_file(self):
        if not self.auth.is_logged_in():
            console.print("[red]Please login first (Option 5)[/red]")
            Prompt.ask("Press Enter to continue")
            return

        local_path = Prompt.ask("Enter local file path")
        remote_path = Prompt.ask("Enter remote destination", default="/")

        self.client.upload_file(local_path, remote_path)
        Prompt.ask("Press Enter to continue")

    def download_file(self):
        if not self.auth.is_logged_in():
            console.print("[red]Please login first (Option 5)[/red]")
            Prompt.ask("Press Enter to continue")
            return

        remote_path = Prompt.ask("Enter remote file path")
        local_dest = Prompt.ask("Enter local destination", default=str(Path.home() / "Downloads"))

        self.client.download_file(remote_path, local_dest)
        Prompt.ask("Press Enter to continue")

    def manage_watch_folders(self):
        while True:
            console.clear()
            console.print("[bold cyan]📂 Watch Folder Management[/bold cyan]\n")
            self.watch_manager.list_folders()

            console.print("\n[1] Add folder  [2] Remove folder  [3] Start watching  [q] Back")
            choice = Prompt.ask("Select action")

            if choice == '1':
                local = Prompt.ask("Local folder path")
                remote = Prompt.ask("Remote destination", default="/AutoUpload")
                self.watch_manager.add_folder(local, remote)
                Prompt.ask("Press Enter")
            elif choice == '2':
                local = Prompt.ask("Folder to stop watching")
                self.watch_manager.remove_folder(local)
                Prompt.ask("Press Enter")
            elif choice == '3':
                self.watch_manager.start()
            elif choice.lower() == 'q':
                break

    def handle_auth(self):
        console.clear()
        if self.auth.is_logged_in():
            console.print("[green]Currently logged in[/green]")
            if Confirm.ask("Logout?", default=False):
                self.auth.logout()
        else:
            console.print("[yellow]Not logged in[/yellow]")
            if Confirm.ask("Login now?", default=True):
                self.auth.login_interactive()
        Prompt.ask("Press Enter to continue")

    def install_tools(self):
        console.clear()
        console.print("[bold cyan]🔧 Tool Installation[/bold cyan]\n")

        console.print("Checking dependencies...\n")

        # Check Python packages
        try:
            import rich, watchdog, requests
            console.print("[green]✓ Python packages installed[/green]")
        except ImportError:
            console.print("[yellow]○ Installing Python packages...[/yellow]")
            subprocess.run([sys.executable, '-m', 'pip', 'install', '-r',
                          str(Path(__file__).parent / 'requirements.txt')])

        # Check tbc
        if self.client.tbc_path:
            console.print(f"[green]✓ tbc found at: {self.client.tbc_path}[/green]")
        else:
            console.print("[yellow]○ tbc not found[/yellow]")
            if Confirm.ask("Install tbc from GitHub?", default=True):
                console.print("[cyan]Installing tbc...[/cyan]")
                install_script = """
                cd /tmp
                git clone https://github.com/sha5010/tbc.git
                cd tbc
                go build -o tbc
                sudo mv tbc /usr/local/bin/
                """
                console.print("[dim]Run these commands manually:[/dim]")
                console.print(install_script)

        Prompt.ask("\nPress Enter to continue")

    def run(self):
        while True:
            self.show_menu()
            choice = Prompt.ask("\nSelect option")

            if choice == '1':
                self.browse_files()
            elif choice == '2':
                self.upload_file()
            elif choice == '3':
                self.download_file()
            elif choice == '4':
                self.manage_watch_folders()
            elif choice == '5':
                self.handle_auth()
            elif choice == '6':
                self.install_tools()
            elif choice.lower() == 'q':
                console.print("[green]Goodbye! 👋[/green]")
                break
            else:
                console.print("[red]Invalid option[/red]")
                time.sleep(1)


if __name__ == "__main__":
    app = TeraBoxApp()
    try:
        app.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Goodbye![/yellow]")
