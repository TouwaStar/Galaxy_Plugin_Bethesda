import sys
if sys.platform == 'win32':
    import winreg
from consts import BETTY_WINREG_LOCATION, BETTY_LAUNCHER_EXE, WINDOWS_UNINSTALL_LOCATION
import os
import re
import logging as log

class LocalClient(object):
    def __init__(self):
        self._is_installed = None
        self._local_id_cache = {}


    @property
    def client_exe_path(self):
        try:
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            with winreg.OpenKey(reg, BETTY_WINREG_LOCATION) as key:
                path = winreg.QueryValueEx(key, "installLocation")[0]
            return os.path.join(path, BETTY_LAUNCHER_EXE)
        except OSError:
            return ""

    @property
    def is_installed(self):
        if sys.platform is not 'win32':
            return False
        try:
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            with winreg.OpenKey(reg, BETTY_WINREG_LOCATION) as key:
                path = winreg.QueryValueEx(key, "installLocation")[0]
            return os.path.exists(os.path.join(path, BETTY_LAUNCHER_EXE))
        except OSError:
            return False

    def get_game_id(self, regex_pattern, value_query):

        # check if in cache
        if regex_pattern in self._local_id_cache:
            return self._local_id_cache[regex_pattern]

        # regex = re.compile(regex_pattern)
        log.info(regex_pattern)
        log.info(value_query)
        local_id = None
        try:
            reg = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
            index = 0
            with winreg.OpenKey(reg, WINDOWS_UNINSTALL_LOCATION) as key:
                while(True):
                    try:
                        subkey_name = winreg.EnumKey(key, index)
                        index += 1
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            log.info( winreg.QueryValueEx(subkey, value_query)[0])
                            if re.search(regex_pattern, winreg.QueryValueEx(subkey, value_query)[0]):
                                log.info("here")
                                unstring = winreg.QueryValueEx(subkey, "UninstallString")[0]
                                local_id = unstring.split('bethesdanet//uninstall/')[1]
                                self._local_id_cache[regex_pattern] = local_id
                                break
                    except OSError:
                        continue
        except OSError:
            pass

        return local_id







