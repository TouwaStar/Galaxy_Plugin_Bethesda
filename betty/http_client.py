
from galaxy.http import HttpClient
from galaxy.api.errors import AuthenticationRequired, AccessDenied
import aiohttp
import logging as log
from yarl import URL
import pickle
import base64
import json


class CookieJar(aiohttp.CookieJar):
    def __init__(self):
        super().__init__()
        self._cookies_updated_callback = None

    def set_cookies_updated_callback(self, callback):
        self._cookies_updated_callback = callback

    def update_cookies(self, cookies, url=URL()):
        super().update_cookies(cookies, url)
        if cookies and self._cookies_updated_callback:
            self._cookies_updated_callback(list(self))


class AuthenticatedHttpClient(HttpClient):

    def __init__(self, store_credentials):
        self._store_credentials = store_credentials
        self.bearer = None
        self.user = None
        self._cookie_jar = CookieJar()
        self._auth_lost_callback = None

        super().__init__(cookie_jar=self._cookie_jar)

    def set_cookies_updated_callback(self, callback):
        self._cookie_jar.set_cookies_updated_callback(callback)

    def update_cookies(self, cookies):
        self._cookie_jar.update_cookies(cookies)

    def set_auth_lost_callback(self, callback):
        self._auth_lost_callback = callback

    def get_credentials(self):
        creds = self.user
        creds['cookie_jar'] = pickle.dumps([c for c in self._cookie_jar]).hex()
        return creds

    async def _authenticate(self, grant_type, secret):
        pass

    async def do_request(self, method, *args, **kwargs):
        try:
            return await self.request(method, *args, **kwargs)
        except Exception as e:
            log.warning(f"Request failed with {repr(e)}, attempting to refresh credentials")
            await self.authenticate()
            return await self.request(method, *args, **kwargs)

    async def authenticate(self):
        url = "https://api.bethesda.net/dwemer/attunement/v1/authenticate"
        cookies_before = self._cookie_jar
        try:
            resp = await self.request("put", url=url)
            resp = await resp.json()
        except (AuthenticationRequired, AccessDenied) as e:
            log.error(repr(e))
            if self._auth_lost_callback:
                self._auth_lost_callback()
            raise
        self.bearer = resp['idToken']
        display_name = "Display_Name"
        user_id = "420"
        try:
            middle_token_part = self.bearer.split('.')[1]+"=="
            decoded_token = base64.b64decode(middle_token_part)
            user_info_json = json.loads(decoded_token.decode("utf-8", "ignore"))
            display_name = user_info_json['username']
            user_id = user_info_json['id']
        except ValueError as e:
            log.exception(f"Unable to parse display_name and user_id {repr(e)}")
        self.user = {'display_name': display_name, 'user_id': user_id}
        self._store_credentials(self.get_credentials())

        # For investigation
        if cookies_before == self._cookie_jar:
            log.info("Cookies after auth are the same as before ")

        return self.user






