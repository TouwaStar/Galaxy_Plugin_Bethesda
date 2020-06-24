import sys
if sys.platform == 'win32':
    import winreg
    import psutil

from threading import Lock, Thread

from consts import BETTY_WINREG_LOCATION, BETTY_LAUNCHER_EXE, WINDOWS_UNINSTALL_LOCATION
from pathlib import Path
import os
import logging as log
import subprocess
from file_read_backwards import FileReadBackwards

import asyncio

class LocalClient(object):
    def __init__(self):
        self._is_installed = None
        self.local_games_cache = {}

        self.installed_games_lock = Lock()
        self.installed_games = {}
        self.installed_games_task = None

        self.clientgame_modify_date = None

        self.betty_client_process = None
        self.betty_client_process_children_len = -1
        self.betty_client_path = None


    @property
    def client_exe_path(self):
        try:
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            with winreg.OpenKey(reg, BETTY_WINREG_LOCATION) as key:
                path = winreg.QueryValueEx(key, "installLocation")[0]
            return os.path.join(path, BETTY_LAUNCHER_EXE)
        except OSError:
            return ""
        except Exception as e:
            log.exception(f"Exception while retrieving client exe path assuming none {repr(e)}")
            return ""

    @property
    def client_clientgame_path(self):
        try:
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            with winreg.OpenKey(reg, BETTY_WINREG_LOCATION) as key:
                path = winreg.QueryValueEx(key, "installLocation")[0]
            return os.path.join(path, "clientgame.dat")
        except OSError:
            return ""
        except Exception as e:
            log.exception(f"Exception while retrieving clientgame path assuming none {repr(e)}")
            return ""

    def clientgame_changed(self):
        clientgame = self.client_clientgame_path
        try:
            if clientgame:
                mtime = os.path.getmtime(clientgame)
                if self.clientgame_modify_date != mtime:
                    self.clientgame_modify_date = mtime
                    return True
        except (OSError, FileNotFoundError):
            return False
        return False

    def focus_client_window(self):
        if sys.platform != 'win32':
            log.error(f"Incompatible platform {sys.platform}")
            return
        subprocess.Popen(self.client_exe_path)

    async def is_running(self):
        for proc in psutil.process_iter():
            await asyncio.sleep(0.10)
            try:
                # Check if process name contains the given name string.
                if "bethesdanetlauncher.exe" in proc.name().lower():
                    self.betty_client_process = proc
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        self.betty_client_process = None
        return False

    def is_installed(self):
        # Bethesda client is not available for macOs
        if sys.platform != 'win32':
            log.info("Platform is not compatible")
            return False
        path = None
        try:
            log.info("Connecting to hkey_local_machine key")
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            log.info(f"Opening key at {reg}, {BETTY_WINREG_LOCATION}")
            with winreg.OpenKey(reg, BETTY_WINREG_LOCATION) as key:
                path = winreg.QueryValueEx(key, "installLocation")[0]
            log.info(f"Checking if path exists at {os.path.join(path, BETTY_LAUNCHER_EXE)}")
            self.betty_client_path = path
            return os.path.exists(os.path.join(path, BETTY_LAUNCHER_EXE))
        except (OSError, KeyError):
            self.betty_client_path = path
            return False
        except Exception as e:
            self.betty_client_path = path
            log.exception(f"Exception while checking if client is installed, assuming not installed {repr(e)}")
            return False

    @staticmethod
    def is_local_game_installed(local_game):

        if not os.path.exists(local_game['path']):
            log.info(f" DOESNT EXIsT {local_game['path']}")
            return False
        for exec in local_game['execs']:
            if not os.path.exists(exec):
                log.info(f" DOESNT EXIsT {exec}")
                return False
        return True

    @staticmethod
    def find_executables(folder):
        folder = Path(folder)
        execs = []
        if not folder.exists():
            log.error(f"{folder} does not exist!")
            return []
        for root, dirs, files in os.walk(folder):
            for path in files:
                whole_path = os.path.join(root, path)
                if path.endswith('.exe'):
                    execs.append(whole_path)
        return execs

    def _check_cached_games(self, products):
        installed_games = {}
        products_for_further_scanning = products.copy()

        # Do a quicker, easier exclude for items which are already in the cache
        for product in products.copy():
            if product in self.local_games_cache:
                try:
                    if self.is_local_game_installed(self.local_games_cache[product]):
                        installed_games[product] = self.local_games_cache[product]['local_id']
                    products_for_further_scanning.pop(product)
                except OSError:
                    products_for_further_scanning.pop(product)
        return installed_games, products_for_further_scanning

    def _scan_games_registry_keys(self, products):
        installed_games = {}
        products_for_further_scanning = products.copy()

        # Open uninstall registry key
        try:
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            win_uninstall_key = winreg.OpenKey(reg, WINDOWS_UNINSTALL_LOCATION)
        except OSError:
            log.error(f"Unable to parse registry for installed games")
            return installed_games, products_for_further_scanning
        except Exception:
            log.exception(f"Unexpected error when parsing registry")
            raise

        log.info(f"Iterating over uninstall key {win_uninstall_key}")
        # Iterate over entries in uninstall registry key
        try:
            for i in range(0, winreg.QueryInfoKey(win_uninstall_key)[0]):
                try:
                    winreg_uninstall_subkey_name = winreg.EnumKey(win_uninstall_key, i)
                    winreg_uninstall_subkey = winreg.OpenKey(win_uninstall_key, winreg_uninstall_subkey_name)
                    for product in products.copy():
                        try:
                            winreg.QueryValueEx(winreg_uninstall_subkey, 'DisplayName')[0]
                        except:
                            continue
                        if product in winreg.QueryValueEx(winreg_uninstall_subkey, 'DisplayName')[0] or product.replace(':', '') in \
                                winreg.QueryValueEx(winreg_uninstall_subkey, 'DisplayName')[0]:
                            if 'bethesdanet://uninstall' in winreg.QueryValueEx(winreg_uninstall_subkey, 'UninstallString')[0]:
                                unstring = winreg.QueryValueEx(winreg_uninstall_subkey, "UninstallString")[0]
                                local_id = unstring.split('bethesdanet://uninstall/')[1]
                                path = winreg.QueryValueEx(winreg_uninstall_subkey, "Path")[0].strip('\"')
                                executables = self.find_executables(path)
                                self.local_games_cache[product] = {'local_id': local_id,
                                                                   'path': path,
                                                                   'execs': executables}
                                installed_games[product] = local_id
                                products_for_further_scanning.pop(product)
                except OSError as e:
                    log.info(f"Encountered OsError while parsing through registry keys {repr(e)}")
                    continue
        except Exception:
            log.exception(f"Unexpected error when parsing registry")
            winreg.CloseKey(winreg_uninstall_subkey)
            winreg.CloseKey(win_uninstall_key)
            raise
        winreg.CloseKey(winreg_uninstall_subkey)
        winreg.CloseKey(win_uninstall_key)

        return installed_games, products_for_further_scanning

    def _find_id_of_last_launched_game(self):
        try:
            with FileReadBackwards(os.path.join(self.betty_client_path, 'logs', 'LauncherLog.log'), encoding="utf-8") as frb:
                for line in frb:
                    is_running_line = line.find("'running' for cdpId ")
                    if is_running_line >= 0:
                        local_id = line[is_running_line + len("'running' for cdpId "):]
                        return str(local_id).replace(' ', '')
        except Exception as e:
            log.error(f"Unable to read client log, probably doesnt exist {repr(e)}")
        return ""

    async def get_size_at_path(self, start_path):
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(start_path):
            for f in filenames:
                await asyncio.sleep(0)
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)

        return total_size

    def _scan_launcher_children(self, products):

        installed_games = {}
        products_for_further_scanning = products.copy()

        try:
            if self.is_installed() and self.is_running():
                log.info("Bethesda client is running")
                for child in self.betty_client_process.children(recursive=True):
                    if child.name().lower() not in 'bethesdanetlauncher.exe':
                        execs = [child.exe()]
                        path = os.path.abspath(os.path.join(child.exe(), ".."))
                        local_id = self._find_id_of_last_launched_game()
                        log.info(f"Found id {local_id}")
                        if not local_id:
                            return installed_games, products_for_further_scanning
                        else:
                            for product in products_for_further_scanning:
                                if products_for_further_scanning[product]['local_id'] == local_id:
                                    self.local_games_cache[product] = {'local_id': local_id,
                                                                       'path': path,
                                                                       'execs': execs}
                                    installed_games[product] = local_id
                                    products_for_further_scanning.pop(product)
                                    return installed_games, products_for_further_scanning
            else:
                return installed_games, products_for_further_scanning
        except Exception as e:
            log.error(f"Exception while scanning bethesda launcher children {repr(e)}")
        return installed_games, products_for_further_scanning

    def _update_installed_games(self, products):
        installed_games, products_to_scan = self._check_cached_games(products)
        log.info(f"Scanned through local games cache {installed_games}")

        found_games, products_to_scan = self._scan_games_registry_keys(products_to_scan)
        installed_games = {**installed_games, **found_games}
        log.info(f"Scanned through registry keys {installed_games}")

        found_games, products_to_scan = self._scan_launcher_children(products_to_scan)
        installed_games = {**installed_games, **found_games}
        log.info(f"Scanned through launcher children {installed_games}")

        log.info(f"Setting {installed_games}")
        self.installed_games_lock.acquire()
        self.installed_games = installed_games
        self.installed_games_lock.release()

    def get_installed_products(self, timeout, products_cache):
        if not self.installed_games_task or not self.installed_games_task.is_alive():
            self.installed_games_task = Thread(target=self._update_installed_games,
                                               args=(products_cache,), daemon=True)
            self.installed_games_task.start()
        else:
            log.info("Installed games task check still alive")

        self.installed_games_task.join(timeout)
        installed_products = {}
        if self.installed_games_lock.acquire(True, 1):
            installed_products = self.installed_games
            self.installed_games_lock.release()
        else:
            log.info("Unable to lock installed_games")
        return installed_products

    def launcher_children_number_changed(self):
        if not self.betty_client_process:
            return False

        children_number = self.betty_client_process_children_len
        if children_number != len(self.betty_client_process.children()):
            self.betty_client_process_children_len = len(self.betty_client_process.children())
            return True
        return False











