"""Component-test server only: first Release gets 503 before CHF processing.

Never import this wrapper in deployment units. Subsequent attempts use the real
CHF application and database; fault injection exercises detached native retry.
"""
from starlette.responses import JSONResponse
from app.main import create_app

inner = create_app()
failed = set()


async def app(scope, receive, send):
    path = scope.get('path', '')
    if scope['type'] == 'http' and path.endswith('/release') and path not in failed:
        failed.add(path)
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect' or not message.get('more_body', False):
                break
        response = JSONResponse({'status': 503, 'cause': 'SYSTEM_FAILURE'}, status_code=503)
        return await response(scope, receive, send)
    return await inner(scope, receive, send)
