# Copyright (C) 2026 YIQISOFT
# SPDX-License-Identifier: Apache-2.0

"""
The device command REST controller - ported from
`device-sdk-go/internal/controller/http/command.go`.

`CommandController` provides the `get_command` and `set_command` handlers registered
for the GET / PUT `/api/v3/device/{name}/{command}` route.  They parse the SDK reserved
query parameters (`ds-regexcommand`, `ds-pushevent`, `ds-returnevent`), forward the
remaining query string to the ProtocolDriver via the application layer
(`application.command_read` / `application.command_write`) and assemble the
EventResponse / BaseResponse packets.

The Go handlers are methods on `RestController` and pull their dependencies from the DI
container; the Python port keeps them as methods of the mixin class below, reading the
`driver`, `configuration` and `device_service` from the `RestController` instance
provided by multiple inheritance (see `router.py`).
"""

from __future__ import annotations

from http import HTTPStatus

from starlette.requests import Request
from starlette.responses import Response

from ...application import command_read, command_write
from ...common.consts import (
    API_DEVICE_COMMAND_ROUTE,
    COMMAND,
    NAME,
    PUSH_EVENT,
    REGEX_COMMAND,
    RETURN_EVENT,
    VALUE_FALSE,
    VALUE_TRUE,
)
from ...common.utils import EdgexError
from ._utils import (
    base_response,
    correlation_id_from_request,
    filter_query_params,
    parse_request_body,
    send_edgx_error,
    send_event_response,
    send_response,
)


class CommandController:
    """The GET / PUT device command handlers of the REST controller.

    Provides `get_command` / `set_command` as bound methods so that they can be
    registered directly on the FastAPI router by the `RestController`.
    """

    def get_command(self, request: Request) -> Response:
        """Handle the GET `/api/v3/device/{name}/{command}` request.

        Mirrors `(c *RestController) GetCommand(e echo.Context)` in command.go: reads the
        device / command names from the URL, filters the reserved query parameters and
        executes the command through `application.command_read`.  When `ds-pushevent=true`
        the resulting Event is pushed to the MessageBus and, unless `ds-returnevent=false`
        is set, the Event is returned in the response.
        """
        device_name = request.path_params.get(NAME, "")
        command_name = request.path_params.get(COMMAND, "")
        correlation_id = correlation_id_from_request(request)

        # parse query parameter
        query_string, reserved = filter_query_params(request.url.query)

        regex_cmd = True
        if reserved.get(REGEX_COMMAND) == VALUE_FALSE:
            regex_cmd = False

        try:
            event = command_read(
                device_name, correlation_id, command_name,
                driver=self.driver,
                configuration=self.configuration,
                attributes=query_string,
                regex_cmd=regex_cmd,
                device_service=self.device_service,
                logger=self.logger)
        except EdgexError as exc:
            return send_edgx_error(request, exc, API_DEVICE_COMMAND_ROUTE)

        # push event to CoreData if specified (default false)
        if reserved.get(PUSH_EVENT) == VALUE_TRUE:
            self.send_event(event, correlation_id)

        # return event in http response if specified (default true)
        if reserved.get(RETURN_EVENT, VALUE_TRUE) in ("", VALUE_TRUE):
            return send_event_response(request, event, HTTPStatus.OK)

        return Response(status_code=HTTPStatus.OK)

    async def set_command(self, request: Request) -> Response:
        """Handle the PUT `/api/v3/device/{name}/{command}` request.

        Mirrors `(c *RestController) SetCommand(e echo.Context)` in command.go: filters
        the reserved query parameters, parses the request body and executes the command
        through `application.command_write`.  The resulting Event (when the command is
        not write-only) is pushed to the MessageBus and a BaseResponse is returned.

        The handler is asynchronous because it reads the request body, which is a
        coroutine in Starlette.
        """
        device_name = request.path_params.get(NAME, "")
        command_name = request.path_params.get(COMMAND, "")
        correlation_id = correlation_id_from_request(request)

        # parse query parameter
        query_string, _ = filter_query_params(request.url.query)

        body = await request.body()
        try:
            request_params = parse_request_body(body)
        except EdgexError as exc:
            return send_edgx_error(request, exc, API_DEVICE_COMMAND_ROUTE)

        try:
            event = command_write(
                device_name, correlation_id, command_name,
                driver=self.driver,
                configuration=self.configuration,
                requests=request_params,
                attributes=query_string,
                device_service=self.device_service,
                logger=self.logger)
        except EdgexError as exc:
            return send_edgx_error(request, exc, API_DEVICE_COMMAND_ROUTE)

        if event is not None:
            self.send_event(event, correlation_id)

        response = base_response("", "", HTTPStatus.OK)
        return send_response(request, response, API_DEVICE_COMMAND_ROUTE,
                             HTTPStatus.OK)
