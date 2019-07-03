import sys

import logging as log
import subprocess
import webbrowser

from galaxy.api.plugin import Plugin, create_and_run_plugin
from galaxy.api.consts import Platform
from galaxy.api.types import NextStep, Authentication, Game, LicenseInfo, LicenseType, LocalGame, LocalGameState
from galaxy.api.errors import InvalidCredentials
from version import __version__
from urllib.parse import unquote

from consts import AUTH_PARAMS
from backend import BethesdaClient
from http_client import AuthenticatedHttpClient
from local import LocalClient
import pickle
import json

class BethesdaPlugin(Plugin):
    def __init__(self, reader, writer, token):
        super().__init__(Platform.Bethesda, __version__, reader, writer, token)
        self._http_client = AuthenticatedHttpClient(self.store_credentials)
        self.bethesda_client = BethesdaClient(self._http_client)
        self.local_client = LocalClient()
        self.products_cache = {}

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
        self.products_cache[reference_id][item] = value

    async def _parse_store_games_info(self):
        store_games_info = await self.bethesda_client.get_store_games_info()

        for game in store_games_info:
            if not game["externalReferenceId"]:
                continue
            self._add_to_product_cache(game["externalReferenceId"], "displayName", game["displayName"])

    async def get_owned_games(self):
        owned_ids = await self.bethesda_client.get_owned_ids()
        log.info(f"Owned Ids: {owned_ids}")

        owned_games_ids = set()
        await self._parse_store_games_info()
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
            if self.products_cache[product]["owned"]:
                games_to_send.append(Game(product, self.products_cache[product]["displayName"], None, LicenseInfo(LicenseType.SinglePurchase)))

        return games_to_send

    async def get_local_games(self):
        return []
        # cache is empty
        # if not self.products_cache:
        #     await self._parse_store_games_info()
        #
        # local_games = []
        #
        # for product in self.products_cache:
        #     rp = f".*{self.products_cache[product]['displayName']}.*"
        #     id = self.local_client.get_game_id(regex_pattern=rp, value_query="DisplayName")
        #     if id:
        #         local_games.append(LocalGame(product, LocalGameState.Installed))
        #
        # return local_games

    async def install_game(self, game_id):
        if not self.local_client.is_installed:
            await self._open_betty_browser()
            return

        cmd = self.local_client.client_exe_path + f" --installproduct={game_id}"
        subprocess.Popen(cmd, shell=True)
 

    async def launch_game(self, game_id):
        if not self.local_client.is_installed:
            await self._open_betty_browser()
            return

        cmd = f"start bethesdanet://run/{game_id}"
        subprocess.Popen(cmd, shell=True)

    async def uninstall_game(self, game_id):
        if not self.local_client.is_installed:
            await self._open_betty_browser()
            return
        cmd = f"start bethesdanet://uninstall/{game_id}"
        subprocess.Popen(cmd, shell=True)

    async def _open_betty_browser(self):
        url = "https://bethesda.net/game/bethesda-launcher"
        log.info(f"Opening Bethesda website on url {url}")
        webbrowser.open(url)

    def shutdown(self):
        self._http_client.close()

def main():
    create_and_run_plugin(BethesdaPlugin, sys.argv)


if __name__ == "__main__":
    main()
