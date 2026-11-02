"""Local-only Swagger transport using the ordinary browser CSRF protocol."""

import json

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse


def local_swagger(openapi_url: str, title: str, csrf_url: str) -> HTMLResponse:
    page = get_swagger_ui_html(openapi_url=openapi_url, title=title)
    interceptor = """
    requestInterceptor: async (request) => {
        const target = new URL(request.url, location.origin);
        if (target.origin !== location.origin) throw new Error('Use same-origin local docs');
        request.credentials = 'include';
        if (!['GET', 'HEAD', 'OPTIONS'].includes((request.method || 'GET').toUpperCase())) {
            const response = await fetch(CSRF_ENDPOINT, {credentials: 'include'});
            if (!response.ok) throw new Error('CSRF initialization failed');
            const data = await response.json();
            request.headers['X-CSRF-Token'] = data.csrf_token;
        }
        return request;
    },
    presets: ["""
    interceptor = interceptor.replace("CSRF_ENDPOINT", json.dumps(csrf_url))
    return HTMLResponse(bytes(page.body).decode().replace("presets: [", interceptor, 1))
