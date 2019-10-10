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

class LocalClient(object):
    def __init__(self):
        self._is_installed = None
        self.local_games_cache = {}

        self.installed_games_lock = Lock()
        self.installed_games = {}
        self.installed_games_task = None

        self.clientgame_modify_date = None

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

    @property
    def is_running(self):
        for proc in psutil.process_iter():
            try:
                # Check if process name contains the given name string.
                if "bethesdanetlauncher.exe" in proc.name().lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return False

    @property
    def is_installed(self):
        # Bethesda client is not available for macOs
        if sys.platform != 'win32':
            log.info("Platform is not compatible")
            return False
        try:
            log.info("Connecting to hkey_local_machine key")
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            log.info(f"Opening key at {reg}, {BETTY_WINREG_LOCATION}")
            with winreg.OpenKey(reg, BETTY_WINREG_LOCATION) as key:
                path = winreg.QueryValueEx(key, "installLocation")[0]
            log.info(f"Checking if path exists at {os.path.join(path, BETTY_LAUNCHER_EXE)}")
            return os.path.exists(os.path.join(path, BETTY_LAUNCHER_EXE))
        except (OSError, KeyError):
            return False
        except Exception as e:
            log.exception(f"Exception while checking if client is installed, assuming not installed {repr(e)}")
            return False

    @staticmethod
    def is_local_game_installed(self, local_game):
        try:
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            with winreg.OpenKey(reg, WINDOWS_UNINSTALL_LOCATION) as key:
                winreg.OpenKey(key, local_game['registry_path'])
                if os.path.exists(local_game['path']):
                    return True
        except OSError:
            return False

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
                    execs.append(whole_path.lower().split('\\')[-1])
        return execs

    def _check_cached_games(self, products):
        installed_games = {}
        products_for_further_scanning = products.copy()
        reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
        with winreg.OpenKey(reg, WINDOWS_UNINSTALL_LOCATION) as key:

            # Do a quicker, easier exclude for items which are already in the cache
            for product in products.copy():
                if product in self.local_games_cache:
                    try:
                        winreg.OpenKey(key, self.local_games_cache[product]['registry_path'])
                        if os.path.exists(self.local_games_cache[product]['path']):
                            installed_games[product] = self.local_games_cache[product]['local_id']
                        products_for_further_scanning.pop(product)
                    except OSError:
                        products_for_further_scanning.pop(product)
        return installed_games, products_for_further_scanning

    def _scan_games_registry_keys(self, products, winreg_uninstall_key, winreg_uninstall_key_name):
        # Try to find installed products retrieved by api requests,
        # use copy because the dict can be modified by other methods since this is an async check
        installed_games = {}
        for product in products.copy():
            try:
                try:
                    winreg.QueryValueEx(winreg_uninstall_key, 'DisplayName')[0]
                except:
                    continue
                if product in winreg.QueryValueEx(winreg_uninstall_key, 'DisplayName')[0] or product.replace(':', '') in \
                        winreg.QueryValueEx(winreg_uninstall_key, 'DisplayName')[0]:
                    if 'bethesdanet://uninstall' in winreg.QueryValueEx(winreg_uninstall_key, 'UninstallString')[0]:
                        unstring = winreg.QueryValueEx(winreg_uninstall_key, "UninstallString")[0]
                        local_id = unstring.split('bethesdanet://uninstall/')[1]
                        path = winreg.QueryValueEx(winreg_uninstall_key, "Path")[0].strip('\"')
                        executables = self.find_executables(path)
                        self.local_games_cache[product] = {'local_id': local_id,
                                                           'registry_path': winreg_uninstall_key_name,
                                                           'path': path,
                                                           'execs': executables}
                        installed_games[product] = local_id
            except OSError as e:
                log.info(f"Encountered OsError while parsing through registry keys {repr(e)}")
                continue
        return installed_games

    def _update_installed_games(self, products):
        installed_games, products_to_scan = self._check_cached_games(products)

        try:
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            with winreg.OpenKey(reg, WINDOWS_UNINSTALL_LOCATION) as key:

                log.info("Scanned through local games cache")
                for i in range(0, winreg.QueryInfoKey(key)[0]):
                    subkey_name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, subkey_name) as subkey:
                        found_games = self._scan_games_registry_keys(products, subkey, subkey_name)
                        installed_games = {**installed_games, **found_games}
        except OSError:
            log.error(f"Unable to parse registry for installed games")
            return installed_games
        except Exception:
            log.exception(f"Unexpected error when parsing registry")
            raise
        log.info(f"Setting {installed_games}")
        self.installed_games_lock.acquire()
        self.installed_games = installed_games
        self.installed_games_lock.release()

    def get_installed_products(self, timeout, products_cache):
        if not self.installed_games_task or self.installed_games_task.isAlive():
            self.installed_games_task = Thread(target=self._update_installed_games,
                                               args=(products_cache,), daemon=True)
            self.installed_games_task.start()

        self.installed_games_task.join(timeout)
        installed_products = {}
        if self.installed_games_lock.acquire(True, 1):
            installed_products = self.installed_games
            self.installed_games_lock.release()
        else:
            log.info("Unable to lock installed_games")
        return installed_products











