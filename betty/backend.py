import asyncio
import logging as log


class BethesdaClient:
    def __init__(self, http_client):
        self.http_client = http_client

    async def get_store_games_info(self, page=1):
        log.info(f"Asking for store games info, page: {page}")
        resp = await self.http_client.do_request('get',
                                                 f"https://api.bethesda.net/dr-gc/v1/shoppers/me/products?apiKey=f06ec13a06cb4faba310c583fa4419ba&expand=all&format=json&pageSize=50&pageNumber={page}&fields=product.id,product.displayName,product.externalReferenceId,product.variations")
        resp = await resp.json()
        games = resp['products']['product']
        if resp['products']['totalResultPages'] > page:
            await asyncio.sleep(0.1)
            games += await self.get_store_games_info(page + 1)
        return games

    async def get_all_games(self):
        headers = {
            'Authorization': "Bearer 26a7d8bae3b004c1847e14418567d527e337c97440ac52113306ac6861036590"
        }
        resp = await self.http_client.do_request('get', f"https://cdn.contentful.com/spaces/rporu91m20dc/environments/master/entries?content_type=game&locale=en&limit=1000", headers=headers)
        resp = await resp.json()
        games = resp['items']
        return games

    async def get_owned_ids(self):
        headers = {
            'Authorization': f"Bearer {self.http_client.bearer}",
            'Content-Type': "application/json"
        }
        resp = await self.http_client.do_request('get', "https://api.bethesda.net/dwemer/attunement/v1/entitlements/details", headers=headers)
        resp = await resp.json()
        ids = []
        for product in resp["entitlementDetails"]:
            ids.append(product["businessID"])
        return ids

    async def get_game_details(self, business_id):
        headers = {
            'Authorization': f"Bearer 26a7d8bae3b004c1847e14418567d527e337c97440ac52113306ac6861036590",
            'Content-Type': "application/json"
        }
        resp = await self.http_client.do_request('get', f"https://cdn.contentful.com/spaces/rporu91m20dc/environments/master/entries?skip=0&order=&include=3&content_type=productCode&locale=en&limit=100&fields.entitlementBusinessId={business_id}", headers=headers)
        resp = await resp.json()
        if 'includes' not in resp:
            return []
        return resp['includes']
