import logging as log
import subprocess
import webbrowser
import sys
import os
if sys.platform == 'win32':
    from galaxy.proc_tools import process_iter, ProcessInfo

from dataclasses import dataclass

from galaxy.api.plugin import Plugin, create_and_run_plugin
from galaxy.api.consts import Platform
from galaxy.api.types import NextStep, Authentication, Game, LicenseInfo, LicenseType, LocalGame, LocalGameState, Cookie
from galaxy.api.errors import InvalidCredentials, UnknownError, BackendError
from version import __version__

from consts import AUTH_PARAMS, JS
from backend import BethesdaClient
from http_client import AuthenticatedHttpClient
from local import LocalClient
from game_cache import product_cache

import pickle
import asyncio
import time
if sys.platform == 'win32':
    import ctypes

if sys.platform == 'win32':
    @dataclass
    class RunningGame:
        execs: {}
        process: ProcessInfo


class BethesdaPlugin(Plugin):
    def __init__(self, reader, writer, token):
        super().__init__(Platform.Bethesda, __version__, reader, writer, token)
        self._http_client = AuthenticatedHttpClient(self.store_credentials)
        self.bethesda_client = BethesdaClient(self._http_client)
        self.local_client = LocalClient()

        self.local_client.local_games_cache = self.persistent_cache.get('local_games')
        if not self.local_client.local_games_cache:
            self.local_client.local_games_cache = {}

        self.products_cache = product_cache
        self.owned_games_cache = None

        self._asked_for_local = False

        self.update_game_running_status_task = None
        self.update_game_installation_status_task = None
        self.betty_client_process_task = None
        self.check_for_new_games_task = None
        self.running_games = {}
        self.launching_lock = None
        self._tick = 1

    async def authenticate(self, stored_credentials=None):
        if not stored_credentials:
            return NextStep("web_session", AUTH_PARAMS, cookies=[Cookie("passedICO", "true", ".bethesda.net")], js=JS)
        try:
            log.info("Got stored credentials")
            cookies = pickle.loads(bytes.fromhex(stored_credentials['cookie_jar']))
            cookies_parsed = []
            for cookie in cookies:
                if cookie.key in cookies_parsed and cookie.domain:
                    self._http_client.update_cookies({cookie.key: cookie.value})
                elif cookie.key not in cookies_parsed:
                    self._http_client.update_cookies({cookie.key: cookie.value})
                cookies_parsed.append(cookie.key)

            log.info("Finished parsing stored credentials, authenticating")
            user = await self._http_client.authenticate()

            self._http_client.set_auth_lost_callback(self.lost_authentication)
            return Authentication(user_id=user['user_id'], user_name=user['display_name'])
        except Exception as e:
            log.error(f"Couldn't authenticate with stored credentials {repr(e)}")
            raise InvalidCredentials()

    async def pass_login_credentials(self, step, credentials, cookies):
        cookiez = {}
        illegal_keys = ['']
        for cookie in cookies:
            if cookie['name'] not in illegal_keys:
                cookiez[cookie['name']] = cookie['value']
        self._http_client.update_cookies(cookiez)

        try:
            user = await self._http_client.authenticate()
        except Exception as e:
            log.error(repr(e))
            raise InvalidCredentials()

        self._http_client.set_auth_lost_callback(self.lost_authentication)
        return Authentication(user_id=user['user_id'], user_name=user['display_name'])

    def _check_for_owned_products(self, owned_ids):
        products_to_consider = [product for product in self.products_cache if
                                'reference_id' in self.products_cache[product]]
        owned_product_ids = []

        for entitlement_id in owned_ids:
            for product in products_to_consider:
                for reference_id in self.products_cache[product]['reference_id']:
                    if entitlement_id in reference_id:
                        self.products_cache[product]['owned'] = True
                        owned_product_ids.append(entitlement_id)
        return owned_product_ids

    async def _get_owned_pre_orders(self, pre_order_ids):
        games_to_send = []
        for pre_order in pre_order_ids:
            pre_order_details = await self.bethesda_client.get_game_details(pre_order)
            if pre_order_details and 'Entry' in pre_order_details:
                entries_to_consider = [entry for entry in pre_order_details['Entry']
                                       if 'fields' in entry and 'productName' in entry['fields']]
                for entry in entries_to_consider:
                    if entry['fields']['productName'] in self.products_cache:
                        self.products_cache[entry['fields']['productName']]['owned'] = True
                    else:
                        games_to_send.append(Game(pre_order, entry['fields']['productName'] +
                                                  " (Pre Order)", None, LicenseInfo(LicenseType.SinglePurchase)))
                    break
        return games_to_send

    def _get_owned_games(self):
        games_to_send = []
        for product in self.products_cache:
            if self.products_cache[product]["owned"] and self.products_cache[product]["free_to_play"]:
                games_to_send.append(Game(self.products_cache[product]['local_id'], product, None, LicenseInfo(LicenseType.FreeToPlay)))
            elif self.products_cache[product]["owned"]:
                games_to_send.append(Game(self.products_cache[product]['local_id'], product, None, LicenseInfo(LicenseType.SinglePurchase)))
        return games_to_send

    async def get_owned_games(self):
        owned_ids = []
        games_to_send = []

        try:
            owned_ids = await self.bethesda_client.get_owned_ids()
        except (UnknownError, BackendError) as e:
            log.warning(f"No owned games detected {repr(e)}")

        log.info(f"Owned Ids: {owned_ids}")
        product_ids = self._check_for_owned_products(owned_ids)
        pre_order_ids = set(owned_ids) - set(product_ids)

        games_to_send.extend(await self._get_owned_pre_orders(pre_order_ids))
        games_to_send.extend(self._get_owned_games())

        log.info(f"Games to send (with free games): {games_to_send}")
        self.owned_games_cache = games_to_send
        return games_to_send

    async def get_local_games(self):
        if sys.platform != 'win32':
            log.error(f"Incompatible platform {sys.platform}")
            return []
        local_games = []

        installed_products = self.local_client.get_installed_products(timeout=2, products_cache=self.products_cache)

        log.info(f"Installed products {installed_products}")
        for product in self.products_cache:
            for installed_product in installed_products:
                if installed_products[installed_product] == self.products_cache[product]['local_id']:
                    self.products_cache[product]['installed'] = True
                    local_games.append(LocalGame(installed_products[installed_product], LocalGameState.Installed))

        self._asked_for_local = True
        log.info(f"Returning local games {local_games}")
        return local_games

    async def install_game(self, game_id):
        if sys.platform != 'win32':
            log.error(f"Incompatible platform {sys.platform}")
            return

        if not self.local_client.is_installed():
            await self._open_betty_browser()
            return

        if not self.local_client.betty_client_process:
            self.local_client.focus_client_window()
            await self.launch_game(game_id)
        else:
            uuid = None
            for product in self.products_cache:

                if self.products_cache[product]['local_id'] == game_id:
                    if self.products_cache[product]['installed']:
                        log.warning("Got install on already installed game, launching")
                        return await self.launch_game(game_id)
                    uuid = "\"" + self.products_cache[product]['uuid'] + "\""
            cmd = "\"" + self.local_client.client_exe_path + "\"" + f" --installproduct={uuid}"
            log.info(f"Calling install game with command {cmd}")
            subprocess.Popen(cmd, shell=True)

    async def launch_game(self, game_id):
        if sys.platform != 'win32':
            log.error(f"Incompatible platform {sys.platform}")
            return
        if not self.local_client.is_installed():
            await self._open_betty_browser()
            return

        for product in self.products_cache:
            if self.products_cache[product]['local_id'] == game_id:
                if not self.products_cache[product]['installed']:
                    if not not self.local_client.betty_client_process:
                        log.warning("Got launch on a not installed game, installing")
                        return await self.install_game(game_id)
                else:
                    if not not self.local_client.betty_client_process:
                        self.launching_lock = time.time() + 45
                    else:
                        self.launching_lock = time.time() + 30
                    self.running_games[game_id] = None
                    self.update_local_game_status(
                        LocalGame(game_id, LocalGameState.Installed | LocalGameState.Running))
                    self.update_game_running_status_task.cancel()

        cmd = f"start bethesdanet://run/{game_id}"
        log.info(f"Calling launch command for id {game_id}, {cmd}")
        subprocess.Popen(cmd, shell=True)

    async def uninstall_game(self, game_id):
        if sys.platform != 'win32':
            log.error(f"Incompatible platform {sys.platform}")
            return

        if not self.local_client.is_installed():
            await self._open_betty_browser()
            return

        for product in self.products_cache:
            if self.products_cache[product]['local_id'] == game_id:
                if not self.products_cache[product]['installed']:
                    return

        log.info(f"Calling uninstall command for id {game_id}")
        cmd = f"start bethesdanet://uninstall/{game_id}"

        subprocess.Popen(cmd, shell=True)

        if not self.local_client.betty_client_process:
            await asyncio.sleep(2)  # QOL, bethesda slowly reacts to uninstall command,
            self.local_client.focus_client_window()

    async def _open_betty_browser(self):
        url = "https://bethesda.net/game/bethesda-launcher"
        log.info(f"Opening Bethesda website on url {url}")
        webbrowser.open(url)

    async def _heavy_installation_status_check(self):
        installed_products = self.local_client.get_installed_products(4, self.products_cache)
        changed = False

        products_cache_installed_products = {}

        for product in self.products_cache:
            if self.products_cache[product]['installed']:
                products_cache_installed_products[product] = self.products_cache[product]['local_id']

        for installed_product in installed_products:
            if installed_product not in products_cache_installed_products:
                self.products_cache[installed_product]["installed"] = True
                self.update_local_game_status(LocalGame(installed_products[installed_product], LocalGameState.Installed))
                changed = True

        for installed_product in products_cache_installed_products:
            if installed_product not in installed_products:
                self.products_cache[installed_product]["installed"] = False
                self.update_local_game_status(LocalGame(products_cache_installed_products[installed_product], LocalGameState.None_))
                changed = True

        return changed

    def _light_installation_status_check(self):
        changed = False
        for local_game in self.local_client.local_games_cache:
            local_game_installed = self.local_client.is_local_game_installed(self.local_client.local_games_cache[local_game])

            if local_game_installed and not self.products_cache[local_game]["installed"]:
                self.products_cache[local_game]["installed"] = True
                self.update_local_game_status(LocalGame(self.local_client.local_games_cache[local_game]['local_id'], LocalGameState.Installed))
                changed = True

            elif not local_game_installed and self.products_cache[local_game]["installed"]:
                self.products_cache[local_game]["installed"] = False
                self.update_local_game_status(LocalGame(self.local_client.local_games_cache[local_game]['local_id'], LocalGameState.None_))
                changed = True

        return changed

    async def update_game_installation_status(self):

        if self.local_client.clientgame_changed() or self.local_client.launcher_children_number_changed():
            await asyncio.sleep(1)
            log.info("Starting heavy installation status check")
            if await self._heavy_installation_status_check():
                # Game status has changed
                self.persistent_cache['local_games'] = self.local_client.local_games_cache
                self.push_cache()
        else:
            if self._light_installation_status_check():
                # Game status has changed
                self.persistent_cache['local_games'] = self.local_client.local_games_cache
                self.push_cache()

    async def _scan_running_games(self, process_iter_interval):
        for process in process_iter():
            await asyncio.sleep(process_iter_interval)
            for local_game in self.local_client.local_games_cache:
                if not process.binary_path:
                    continue
                if process.binary_path in self.local_client.local_games_cache[local_game]['execs']:
                    log.info(f"Found a running game! {local_game}")
                    local_id = self.local_client.local_games_cache[local_game]['local_id']
                    if local_id not in self.running_games:
                        self.update_local_game_status(LocalGame(local_id,
                                                                LocalGameState.Installed | LocalGameState.Running))
                    self.running_games[local_id] = RunningGame(self.local_client.local_games_cache[local_game]['execs'], process)

    async def _update_status_of_already_running_games(self, process_iter_interval, dont_downgrade_status):
        for running_game in self.running_games.copy():
            if not self.running_games[running_game] and dont_downgrade_status:
                log.info(f"Found 'just launched' game {running_game}")
                continue
            elif not self.running_games[running_game]:
                log.info(f"Found 'just launched' game but its still without pid and its time run out {running_game}")
                self.running_games.pop(running_game)
                self.update_local_game_status(
                    LocalGame(running_game, LocalGameState.Installed))
                continue

            for process in process_iter():
                await asyncio.sleep(process_iter_interval)
                if process.binary_path in self.running_games[running_game].execs:
                    return True

            self.running_games.pop(running_game)
            self.update_local_game_status(
                LocalGame(running_game, LocalGameState.Installed))

    async def update_game_running_status(self):

        process_iter_interval = 0.02
        dont_downgrade_status = False

        if self.launching_lock and self.launching_lock >= time.time():
            process_iter_interval = 0.01
            dont_downgrade_status = True

        if self.running_games:
            # Don't iterate over processes if a game is already running, assuming user is playing one game at a time.
            if not await self._update_status_of_already_running_games(process_iter_interval, dont_downgrade_status):
                await self._scan_running_games(process_iter_interval)
            await asyncio.sleep(1)
            return

        await self._scan_running_games(process_iter_interval)
        await asyncio.sleep(1)

    async def check_for_new_games(self):
        games_cache = self.owned_games_cache
        owned_games = await self.get_owned_games()
        for owned_game in owned_games:
            if owned_game not in games_cache:
                self.add_game(owned_game)
        await asyncio.sleep(60)

    async def close_bethesda_window(self):
        if sys.platform != 'win32':
            return
        window_name = "Bethesda.net Launcher"
        max_delay = 10
        intermediate_sleep = 0.05
        stop_time = time.time() + max_delay

        def timed_out():
            if time.time() >= stop_time:
                log.warning(f"Timed out trying to close {window_name}")
                return True
            return False

        try:
            hwnd = ctypes.windll.user32.FindWindowW(None, window_name)
            while not ctypes.windll.user32.IsWindowVisible(hwnd):
                hwnd = ctypes.windll.user32.FindWindowW(None, window_name)
                await asyncio.sleep(intermediate_sleep)
                if timed_out():
                    return

            while ctypes.windll.user32.IsWindowVisible(hwnd):
                await asyncio.sleep(intermediate_sleep)
                ctypes.windll.user32.CloseWindow(hwnd)
                if timed_out():
                    return
        except Exception as e:
            log.error(f"Exception when checking if window is visible {repr(e)}")

    async def shutdown_platform_client(self):
        if sys.platform != 'win32':
            return
        log.info("killing bethesda")
        subprocess.Popen("taskkill.exe /im \"BethesdaNetLauncher.exe\"")

    async def launch_platform_client(self):
        if not self.local_client.betty_client_process:
            return
        if sys.platform != 'win32':
            return
        log.info("launching bethesda")
        subprocess.Popen('start bethesdanet://', shell=True)
        asyncio.create_task(self.close_bethesda_window())

    def tick(self):
        if sys.platform == 'win32':
            if self._asked_for_local and (not self.update_game_installation_status_task or self.update_game_installation_status_task.done()):
                self.update_game_installation_status_task = asyncio.create_task(self.update_game_installation_status())

            if self._asked_for_local and (not self.update_game_running_status_task or self.update_game_running_status_task.done()):
                self.update_game_running_status_task = asyncio.create_task(self.update_game_running_status())

            if not self.betty_client_process_task or self.betty_client_process_task.done():
                self.betty_client_process_task = asyncio.create_task(self.local_client.is_running())

        if self.owned_games_cache and (not self.check_for_new_games_task or self.check_for_new_games_task.done()):
            self.check_for_new_games_task = asyncio.create_task(self.check_for_new_games())

    async def shutdown(self):
        await self._http_client.close()


def main():
    create_and_run_plugin(BethesdaPlugin, sys.argv)


if __name__ == "__main__":
    main()
