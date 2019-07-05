import sys

import logging as log
import subprocess
import webbrowser

from galaxy.api.plugin import Plugin, create_and_run_plugin
from galaxy.api.consts import Platform
from galaxy.api.types import NextStep, Authentication, Game, LicenseInfo, LicenseType, LocalGame, LocalGameState
from galaxy.api.errors import InvalidCredentials, UnknownError
from version import __version__
from urllib.parse import unquote

from consts import AUTH_PARAMS
from backend import BethesdaClient
from http_client import AuthenticatedHttpClient
from local import LocalClient
import pickle
import json
import asyncio


class BethesdaPlugin(Plugin):
    def __init__(self, reader, writer, token):
        super().__init__(Platform.Bethesda, __version__, reader, writer, token)
        self._http_client = AuthenticatedHttpClient(self.store_credentials)
        self.bethesda_client = BethesdaClient(self._http_client)
        self.local_client = LocalClient()
        self.products_cache = {'8': {'owned': True, 'installed': False, 'displayName': 'Fallout Shelter',
                                     'free_to_play': True},
                               '5': {'owned': True, 'installed': False, 'displayName': 'The Elder Scrolls Legends',
                                     'free_to_play': True},
                               '11': {'owned': True, 'installed': False, 'displayName': 'Quake Champions',
                                     'free_to_play': True}
                               }
        self._asked_for_local = False
        self.parse_store_games_info_task = None

    async def authenticate(self, stored_credentials=None):
        if not stored_credentials:
            return NextStep("web_session", AUTH_PARAMS)
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

            user = {'buid': stored_credentials['buid'],
                    'username': stored_credentials['username']}

            self._http_client.user = user
            log.info("Finished parsing stored credentials, authenticating")
            await self._http_client.authenticate()

            return Authentication(user_id=user['buid'], user_name=user['username'])
        except Exception as e:
            log.error(f"Couldn't authenticate with stored credentials {repr(e)}")
            raise InvalidCredentials()

    async def pass_login_credentials(self, step, credentials, cookies):
        user = None
        cookiez = {}
        for cookie in cookies:
            if cookie['name'] == 'bnet-username':
                user = json.loads(unquote(cookie['value']))
            cookiez[cookie['name']] = cookie['value']

        self._http_client.update_cookies(cookiez)

        try:
            self._http_client.user = user
            await self._http_client.authenticate()
        except Exception as e:
            log.error(repr(e))
            raise InvalidCredentials()

        return Authentication(user_id=user['buid'], user_name=user['username'])

    def _game_is_preorder(self, all_games, game):
        pass

    def _add_to_product_cache(self, reference_id, item, value):
        if reference_id not in self.products_cache:
            self.products_cache[reference_id] = {}
            self.products_cache[reference_id]["owned"] = False
            self.products_cache[reference_id]["installed"] = False
            self.products_cache[reference_id]["free_to_play"] = False
        self.products_cache[reference_id][item] = value

    async def _parse_store_games_info(self):
        try:
            store_games_info = await self.bethesda_client.get_store_games_info()

            for game in store_games_info:
                await asyncio.sleep(0.05)
                if not game["externalReferenceId"]:
                    continue
                self._add_to_product_cache(game["externalReferenceId"], "displayName", game["displayName"])
        except Exception as e:
            log.exception(f"Exception occured while parsing store game info {repr(e)}")

    async def get_owned_games(self):
        """ First we retrieve the owned games entitlements from bethesda api.
        then we parse bethesda store information. And match it with our ids.
        We add all the matches and all the free games to the list of owned games which is then the return value."""
        owned_ids = None
        try:
            owned_ids = await self.bethesda_client.get_owned_ids()
        except UnknownError as e:
            log.warning(f"No owned games detected {repr(e)}")

        log.info(f"Owned Ids: {owned_ids}")

        owned_games_ids = set()

        if not self.parse_store_games_info_task or self.parse_store_games_info_task.done():
            self.parse_store_games_info_task = asyncio.create_task(self._parse_store_games_info())
        await self.parse_store_games_info_task

        log.info(f"Parsed store games information, got {self.products_cache}")

        for product in self.products_cache:
            for owned_id in owned_ids:
                if owned_id in owned_games_ids:
                    continue
                if product == owned_id:
                    owned_games_ids.add(product)
                    self._add_to_product_cache(product, "owned", True)
        log.info(f"Parsed product ownership, cache: {self.products_cache}")

        games_to_send = []
        for product in self.products_cache:
            if self.products_cache[product]["owned"] and self.products_cache[product]["free_to_play"]:
                games_to_send.append(Game(product, self.products_cache[product]["displayName"], None, LicenseInfo(LicenseType.FreeToPlay)))
            elif self.products_cache[product]["owned"]:
                games_to_send.append(Game(product, self.products_cache[product]["displayName"], None, LicenseInfo(LicenseType.SinglePurchase)))

        return games_to_send

    async def get_local_games(self):
        if not self.parse_store_games_info_task or self.parse_store_games_info_task.done():
            self.parse_store_games_info_task = asyncio.create_task(self._parse_store_games_info())
        await self.parse_store_games_info_task

        local_games = []
        installed_products = await self.local_client.get_installed_games(self.products_cache)

        for product in installed_products:
            self._add_to_product_cache(product, "installed", True)
            local_games.append(LocalGame(product, LocalGameState.Installed))

        self._asked_for_local = True
        return local_games

    async def install_game(self, game_id):
        if not self.local_client.is_installed:
            await self._open_betty_browser()
            return

        cmd = self.local_client.client_exe_path + f" --installproduct={game_id}"
        subprocess.Popen(cmd, shell=True)

    def get_local_id(self, game_id):


        for found_game_id in self.local_client.local_games_cache:
            if game_id == found_game_id:
                return self.local_client.local_games_cache[found_game_id]['local_id']
            elif game_id == self.local_client.local_games_cache[found_game_id]['local_id']:
                return game_id

        log.warning(f"Couldn't find a local id to match with {game_id}")
        return None

    async def launch_game(self, game_id):
        if not self.local_client.is_installed:
            await self._open_betty_browser()
            return

        local_id = self.get_local_id(game_id)

        if not local_id:
            await self._open_betty_browser(game_id)
            return

        log.info(f"Calling launch command for id {local_id}")
        cmd = f"start bethesdanet://run/{local_id}"
        subprocess.Popen(cmd, shell=True)

    async def uninstall_game(self, game_id):
        if not self.local_client.is_installed:
            await self._open_betty_browser()
            return

        local_id = self.get_local_id(game_id)

        if not local_id:
            await self._open_betty_browser(game_id)
            return

        log.info(f"Calling uninstall command for id {local_id}")
        cmd = f"start bethesdanet://uninstall/{local_id}"
        subprocess.Popen(cmd, shell=True)
        self.local_client.focus_client_window()

    async def _open_betty_browser(self, game_id=None):
        if game_id:
            url = f"https://bethesda.net/en/games/{game_id}"
        else:
            url = "https://bethesda.net/game/bethesda-launcher"
        log.info(f"Opening Bethesda website on url {url}")
        webbrowser.open(url)

    def shutdown(self):
        self._http_client.close()

    async def _heavy_installation_status_check(self):
        installed_products = await self.local_client.get_installed_games(self.products_cache)
        for installed_product in installed_products:
            if not self.products_cache[installed_product]["installed"]:
                self.products_cache[installed_product]["installed"] = True
                self.update_local_game_status(LocalGame(installed_product, LocalGameState.Installed))
        for product in self.products_cache:
            if self.products_cache[product]["installed"] and product not in installed_products:
                self.products_cache[product]["installed"] = False
                self.update_local_game_status(LocalGame(product, LocalGameState.None_))

    def _light_installation_status_check(self):
        for local_game in self.local_client.local_games_cache:
            local_game_installed = self.local_client.is_local_game_installed(self.local_client.local_games_cache[local_game])
            if local_game_installed and not self.products_cache[local_game]["installed"]:
                self.products_cache[local_game]["installed"] = True
                self.update_local_game_status(LocalGame(local_game, LocalGameState.Installed))
            elif not local_game_installed and self.products_cache[local_game]["installed"]:
                self.products_cache[local_game]["installed"] = False
                self.update_local_game_status(LocalGame(local_game, LocalGameState.None_))



    async def update_game_installation_status(self):
        self._asked_for_local = False

        if self.local_client.clientgame_changed():
            await self._heavy_installation_status_check()
        else:
            self._light_installation_status_check()

        self._asked_for_local = True


    def tick(self):
        if self._asked_for_local:
            asyncio.create_task(self.update_game_installation_status())

def main():
    create_and_run_plugin(BethesdaPlugin, sys.argv)


if __name__ == "__main__":
    main()
