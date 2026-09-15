from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    """Bound bytes even without Content-Length, before JSON parsing."""
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH'}:
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > self.max_bytes:
                response = JSONResponse({'status': 413, 'cause': 'PAYLOAD_TOO_LARGE',
                                         'title': 'Request exceeds profile limit'}, status_code=413,
                                        media_type='application/problem+json')
                return await response(scope, receive, send)
            chunks.append(message.get('body', b''))
            if not message.get('more_body', False):
                break
        pending = True

        async def bounded_receive():
            nonlocal pending
            if pending:
                pending = False
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()

        return await self.app(scope, bounded_receive, send)
