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
from game_cache import product_cache
import pickle
import json
import asyncio



class BethesdaPlugin(Plugin):
    def __init__(self, reader, writer, token):
        super().__init__(Platform.Bethesda, __version__, reader, writer, token)
        self._http_client = AuthenticatedHttpClient(self.store_credentials)
        self.bethesda_client = BethesdaClient(self._http_client)
        self.local_client = LocalClient()
        self.products_cache = product_cache
        self._asked_for_local = False

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

    async def get_owned_games(self):
        owned_ids = None
        try:
            owned_ids = await self.bethesda_client.get_owned_ids()
        except UnknownError as e:
            log.warning(f"No owned games detected {repr(e)}")

        log.info(f"Owned Ids: {owned_ids}")
        matched_ids = []

        if owned_ids:
            for entitlement_id in owned_ids:
                for product in self.products_cache:
                    if 'reference_id' in self.products_cache[product]:
                        if entitlement_id == self.products_cache[product]['reference_id']:
                            self.products_cache[product]['owned'] = True
                            matched_ids.append(entitlement_id)

        pre_orders = set(owned_ids) - set(matched_ids)
        games_to_send = []

        for pre_order in pre_orders:
            pre_order_details = await self.bethesda_client.get_game_details(pre_order)
            if pre_order_details and 'Entry' in pre_order_details:
                for entry in pre_order_details['Entry']:
                    if 'fields' in entry and 'productName' in entry['fields']:
                        games_to_send.append(Game(pre_order, entry['fields']['productName']+" (Pre Order)", None, LicenseInfo(LicenseType.SinglePurchase)))
                        break

        for product in self.products_cache:
            if self.products_cache[product]["owned"] and self.products_cache[product]["free_to_play"]:
                games_to_send.append(Game(self.products_cache[product]['local_id'], product, None, LicenseInfo(LicenseType.FreeToPlay)))
            elif self.products_cache[product]["owned"]:
                games_to_send.append(Game(self.products_cache[product]['local_id'], product, None, LicenseInfo(LicenseType.SinglePurchase)))

        log.info(f"Games to send (with free games): {games_to_send}")

        return games_to_send

    async def get_local_games(self):
        local_games = []
        installed_products = self.local_client.get_installed_games(self.products_cache)
        log.info(f"Installed products {installed_products}")
        for product in self.products_cache:
            for installed_product in installed_products:
                if installed_products[installed_product] == self.products_cache[product]['local_id']:
                    self.products_cache[product]['installed'] = True
                    local_games.append(LocalGame(installed_product, LocalGameState.Installed))

        self._asked_for_local = True
        log.info(f"Returning local games {local_games}")
        return local_games

    async def install_game(self, game_id):
        if not self.local_client.is_installed:
            await self._open_betty_browser()
            return

        if self.local_client.is_running:
            self.local_client.focus_client_window()
            await self.launch_game(game_id)
        else:
            uuid = None
            for product in self.products_cache:
                if self.products_cache[product]['local_id'] == game_id:
                    uuid = "\"" + self.products_cache[product]['uuid'] + "\""
            cmd = "\"" + self.local_client.client_exe_path + "\"" + f" --installproduct={uuid}"
            log.info(f"Calling install game with command {cmd}")
            subprocess.Popen(cmd, shell=True)

    async def launch_game(self, game_id):
        if not self.local_client.is_installed:
            await self._open_betty_browser()
            return

        log.info(f"Calling launch command for id {game_id}")
        cmd = f"start bethesdanet://run/{game_id}"
        subprocess.Popen(cmd, shell=True)

    async def uninstall_game(self, game_id):
        if not self.local_client.is_installed:
            await self._open_betty_browser()
            return

        log.info(f"Calling uninstall command for id {game_id}")
        cmd = f"start bethesdanet://uninstall/{game_id}"
        subprocess.Popen(cmd, shell=True)
        self.local_client.focus_client_window()

    async def _open_betty_browser(self):
        url = "https://bethesda.net/game/bethesda-launcher"
        log.info(f"Opening Bethesda website on url {url}")
        webbrowser.open(url)

    async def _heavy_installation_status_check(self):
        installed_products = self.local_client.get_installed_games(self.products_cache)
        products_cache_installed_products = {}

        for product in self.products_cache:
            if self.products_cache[product]['installed']:
                products_cache_installed_products[product] = self.products_cache[product]['local_id']

        for installed_product in installed_products:
            if installed_product not in products_cache_installed_products:
                self.products_cache[installed_product]["installed"] = True
                self.update_local_game_status(LocalGame(installed_products[installed_product], LocalGameState.Installed))

        for installed_product in products_cache_installed_products:
            if installed_product not in installed_products:
                self.products_cache[installed_product]["installed"] = False
                self.update_local_game_status(LocalGame(products_cache_installed_products[installed_product], LocalGameState.None_))

    def _light_installation_status_check(self):
        for local_game in self.local_client.local_games_cache:
            local_game_installed = self.local_client.is_local_game_installed(self.local_client.local_games_cache[local_game])
            if local_game_installed and not self.products_cache[local_game]["installed"]:
                self.products_cache[local_game]["installed"] = True
                self.update_local_game_status(LocalGame(self.local_client.local_games_cache[local_game]['local_id'], LocalGameState.Installed))
            elif not local_game_installed and self.products_cache[local_game]["installed"]:
                self.products_cache[local_game]["installed"] = False
                self.update_local_game_status(LocalGame(self.local_client.local_games_cache[local_game]['local_id'], LocalGameState.None_))

    async def update_game_installation_status(self):
        self._asked_for_local = False

        if self.local_client.clientgame_changed():
            await asyncio.sleep(1)
            await self._heavy_installation_status_check()
        else:
            self._light_installation_status_check()

        self._asked_for_local = True

    def tick(self):
        if self._asked_for_local:
            asyncio.create_task(self.update_game_installation_status())

    def shutdown(self):
        asyncio.create_task(self._http_client.close())


def main():
    create_and_run_plugin(BethesdaPlugin, sys.argv)


if __name__ == "__main__":
    main()
