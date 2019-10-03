import asyncio
from collections import namedtuple
from collections.abc import Iterable
import logging
import json

from galaxy.api.jsonrpc import (
    JsonRpcError, ParseError, InvalidRequest, MethodNotFound, InvalidParams,
    ApplicationError, UnknownError
)

Method = namedtuple("Method", ["callback", "internal", "sensitive_params"])

def anonymise_sensitive_params(params, sensitive_params):
    anomized_data = "****"
    if not sensitive_params:
        return params

    if isinstance(sensitive_params, Iterable):
        anomized_params = params.copy()
        for key in anomized_params.keys():
            if key in sensitive_params:
                anomized_params[key] = anomized_data
        return anomized_params

    return anomized_data

class IPCProtocol:
    def __init__(self, reader, writer, encoder=json.JSONEncoder()):
        self._active = True
        self._task = None
        self._last_id = 0
        self._reader = reader
        self._writer = writer
        self._encoder = encoder
        self._methods = {}
        self._notifications = {}
        self._eof_listeners = []
        self._requests_futures = {}

    def notify(self, method, params, sensitive_params=False):
        """
        Send notification
        :param method:
        :param params:
        :param sensitive_params: list of parameters that will by anonymized before logging; if False - no params
        are considered sensitive, if True - all params are considered sensitive
        """
        logging.info(
            "Sending notification: method=%s, params=%s",
            method, anonymise_sensitive_params(params, sensitive_params)
        )
        asyncio.create_task(self._send_notification(method, params))

    async def request(self, method, params, sensitive_params=False):
        self._last_id += 1
        request_id = str(self._last_id)
        logging.info(
            "Sending request: id=%s, method=%s, params=%s",
            request_id, method, anonymise_sensitive_params(params, sensitive_params)
        )
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._requests_futures[self._last_id] = future
        await self._send_request(request_id, method, params)
        return await future

    def register_method(self, name, callback, internal, sensitive_params=False):
        """
        Register method
        :param name:
        :param callback:
        :param internal: if True the callback will be processed immediately (synchronously)
        :param sensitive_params: list of parameters that will by anonymized before logging; if False - no params
        are considered sensitive, if True - all params are considered sensitive
        """
        self._methods[name] = Method(callback, internal, sensitive_params)

    def register_notification(self, name, callback, internal, sensitive_params=False):
        """
        Register notification
        :param name:
        :param callback:
        :param internal: if True the callback will be processed immediately (synchronously)
        :param sensitive_params: list of parameters that will by anonymized before logging; if False - no params
        are considered sensitive, if True - all params are considered sensitive
        """
        self._notifications[name] = Method(callback, internal, sensitive_params)

    def register_eof(self, callback):
        self._eof_listeners.append(callback)

    def start(self):
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    def stop(self):
        self._active = False
        self._writer.close()

    async def wait_stopped(self):
        await self._task
        await self._writer.wait_closed()

    async def run_forever(self):
        self.start()
        await self.wait_stopped()

    async def _run(self):
        while self._active:
            try:
                data = await self._reader.readline()
                if not data:
                    self._eof()
                    continue
            except:
                logging.exception("Failed to read line from input")
                self._eof()
                continue
            data = data.strip()
            logging.debug("Received %d bytes of data", len(data))
            await self._handle_input(data)

    def _eof(self):
        logging.info("Received EOF")
        self.stop()
        for listener in self._eof_listeners:
            listener()

    async def _handle_input(self, data):
        logging.debug("Received input: %s", data) # TODO
        def extract(obj, field):
            try:
                return obj[field]
            except KeyError:
                raise InvalidRequest()

        try:
            try:
                message = json.loads(data, encoding="utf-8")
            except json.JSONDecodeError:
                raise ParseError()

            if message.get("jsonrpc") != "2.0":
                raise InvalidRequest()

            if "result" in message:
                # id, result
                request_id = extract(message, "id")
                result = extract(message, "result")
                self._handle_response(request_id, result)
            elif "error" in message:
                request_id = extract(message, "id")
                error = extract(message, "error")
                code = extract(error, "code")
                message = extract(error, "message")
                data = error.get("data")
                self._handle_error(request_id, code, message, data)
            elif "id" in message:
                request_id = extract(message, "id")
                method = extract(message, "method")
                params = message.get("params", {})
                await self._handle_request(request_id, method, params)
            else:
                method = extract(message, "method")
                params = message.get("params", {})
                self._handle_notification(method, params)
        except JsonRpcError as error:
            await self._send_error(None, error)

    def _handle_response(self, request_id, result):
        future = self._requests_futures.get(int(request_id))
        if future is None:
            logging.warning("Received response for unknown request: %s", request_id)
            return
        future.set_result(result)

    def _handle_error(self, request_id, code, message, data):
        future = self._requests_futures.get(int(request_id))
        if future is None:
            logging.warning("Received error for unknown request: %s", request_id)
            return
        future.set_exception(JsonRpcError(code, message, data))

    def _handle_notification(self, method_name, params):
        method = self._notifications.get(method_name)
        if not method:
            logging.warning("Received unexpected notification: %s", method_name)
            return

        callback, internal, sensitive_params = method
        logging.info(
            "Handling notification: method=%s, params=%s",
            method, anonymise_sensitive_params(params, sensitive_params)
        )

        if internal:
            # internal requests are handled immediately
            callback(**params)
        else:
            try:
                asyncio.create_task(callback(**params))
            except Exception:
                logging.exception("Unexpected exception raised in notification handler")

    async def _handle_request(self, request_id, method_name, params):
        method = self._methods.get(method_name)
        if not method:
            logging.error("Received unexpected request: %s", method_name)
            await self._send_error(request_id, MethodNotFound())
            return

        callback, internal, sensitive_params = method
        logging.info(
            "Handling request: id=%s, method=%s, params=%s",
            request_id, method, anonymise_sensitive_params(params, sensitive_params)
        )

        if internal:
            # internal requests are handled immediately
            response = callback(params)
            await self._send_response(request_id, response)
        else:
            async def handle():
                try:
                    result = await callback(params)
                    await self._send_response(request_id, result)
                except TypeError:
                    await self._send_error(request_id, InvalidParams())
                except NotImplementedError:
                    await self._send_error(request_id, MethodNotFound())
                except ApplicationError as error:
                    await self._send_error(request_id, error)
                except Exception as e: #pylint: disable=broad-except
                    logging.exception("Unexpected exception raised in plugin handler")
                    await self._send_error(request_id, UnknownError(str(e)))

            asyncio.create_task(handle())

    async def _send(self, data):
        try:
            line = self._encoder.encode(data)
            logging.debug("Sending data: %s", line) # TODO
            data = (line + "\n").encode("utf-8")
            self._writer.write(data)
            await self._writer.drain()
        except TypeError as error:
            logging.error(str(error))

    async def _send_response(self, request_id, result):
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": result
        }
        await self._send(message)

    async def _send_error(self, request_id, error):
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": error.code,
                "message": error.message
            }
        }
        if error.data is not None:
            message["error"]["data"] = error.data
        await self._send(message)
        
    async def _send_notification(self, method, params):
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        await self._send(message)

    async def _send_request(self, request_id, method, params):
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params
        }
        await self._send(message)