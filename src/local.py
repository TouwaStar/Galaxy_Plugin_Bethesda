import sys
if sys.platform == 'win32':
    import winreg
from consts import BETTY_WINREG_LOCATION, BETTY_LAUNCHER_EXE, WINDOWS_UNINSTALL_LOCATION
import os
import logging as log

FREE_GAMES = {
    'Fallout Shelter': '8',
    'The Elder Scrolls Legends': '5',
    'Quake Champions': '11'
}

class LocalClient(object):
    def __init__(self):
        self._is_installed = None
        self.local_id_cache = {}


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
    def is_installed(self):
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

    def get_installed_games(self, products):
        installed_games = []
        try:
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            with winreg.OpenKey(reg, WINDOWS_UNINSTALL_LOCATION) as key:
                for i in range(0, winreg.QueryInfoKey(key)[0]):
                    subkey_name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, subkey_name) as subkey:
                        for product in products:
                            try:
                                if products[product]['displayName'] in winreg.QueryValueEx(subkey, 'DisplayName')[0]:
                                    if 'bethesdanet://uninstall' in winreg.QueryValueEx(subkey, 'UninstallString')[0]:
                                        unstring = winreg.QueryValueEx(subkey, "UninstallString")[0]
                                        local_id = unstring.split('bethesdanet://uninstall/')[1]
                                        self.local_id_cache[product] = local_id
                                        installed_games.append(product)
                            except OSError:
                                continue
                        for free_game in FREE_GAMES:
                            try:
                                if free_game in winreg.QueryValueEx(subkey, 'DisplayName')[0]:
                                    if 'bethesdanet://uninstall' in winreg.QueryValueEx(subkey, 'UninstallString')[0]:
                                        installed_games.append(FREE_GAMES[free_game])
                            except OSError:
                                continue
        except OSError as e:
            log.error(f"Unable to parse registry for installed games {repr(e)}")
            return installed_games
        except Exception as e:
            log.exception(f"Unexpected error when parsing registry {repr(e)}")
            raise
        return installed_games







