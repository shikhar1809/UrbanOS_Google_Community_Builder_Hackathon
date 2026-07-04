from fastapi import FastAPI
from firebase_functions import https_fn
import firebase_admin

firebase_admin.initialize_app()
app = FastAPI()

from a2wsgi import ASGIMiddleware
from flask import Response, Request

wsgi_app = ASGIMiddleware(app)

@https_fn.on_request()
def api(req: https_fn.Request) -> https_fn.Response:
    environ = req.environ
    status = []
    headers = []

    def start_response(s, h, exc_info=None):
        status.append(s)
        headers.extend(h)
        return lambda body_data: None

    result = wsgi_app(environ, start_response)
    body_chunks = list(result)
    status_code = int(status[0].split()[0])
    body = b"".join(body_chunks)
    return Response(body, status=status_code, headers=headers)

print("Function registered:", api)
