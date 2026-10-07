"""Local HTTP boundary shared by the browser and native window client."""

import hmac


def request_error(handler, *, mutation=False, multipart=False):
    port = handler.server.server_port
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{host}" for host in hosts}
    host_headers = handler.headers.get_all("Host", [])
    origin_headers = handler.headers.get_all("Origin", [])
    if len(host_headers) != 1 or host_headers[0] not in hosts:
        return 403, "ローカルの接続先ではありません"
    if len(origin_headers) > 1 or (origin_headers and origin_headers[0] not in origins):
        return 403, "別のサイトからのアクセスは許可されていません"
    if not mutation:
        return None
    token_headers = handler.headers.get_all("X-CSRF-Token", [])
    expected = getattr(handler.server, "csrf_token", "")
    if not expected or len(token_headers) != 1 or not token_headers[0].isascii() or not hmac.compare_digest(token_headers[0], expected):
        return 403, "画面を再読み込みしてから操作してください"
    if handler.headers.get_all("Transfer-Encoding", []):
        return 400, "未対応の送信形式です"
    lengths = handler.headers.get_all("Content-Length", [])
    if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
        return 400, "送信サイズが不正です"
    limit = 100 * 1024 * 1024 if multipart else 1_000_000
    if len(lengths[0]) > 10 or int(lengths[0]) > limit:
        return 413, "送信サイズが大きすぎます"
    media_types = handler.headers.get_all("Content-Type", [])
    expected_type = "multipart/form-data" if multipart else "application/json"
    if len(media_types) != 1 or media_types[0].split(";", 1)[0].strip().lower() != expected_type:
        return 415, "未対応の送信形式です"
    return None
