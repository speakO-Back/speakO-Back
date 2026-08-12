"""요청 본문 크기를 ASGI 레벨에서 제한하는 미들웨어.

왜 별도로 필요한가: `main._save_upload_with_limit`는 1MB씩 스트리밍하며 413을 내지만,
**그 함수가 실행되는 시점엔 이미 늦었다.** Starlette/`python-multipart`가 요청 본문 전체를
파싱해 `UploadFile`(디스크로 스필되는 `SpooledTemporaryFile`)에 담아둔 뒤에야 핸들러가
호출되기 때문이다. 즉 수 GB짜리 본문을 보내면 413이 뜨기 전에 디스크가 먼저 찬다.
여기서는 라우팅·파싱이 일어나기 전에 끊는다.
"""
import json


class MaxBodySizeMiddleware:
    """본문이 max_bytes를 넘으면 413으로 끊는다.

    두 단계로 막는다.
      1) `Content-Length` 헤더가 상한을 넘으면 **본문을 한 바이트도 받기 전에** 거절한다.
         브라우저·curl의 파일 업로드는 사실상 전부 이 헤더를 보내므로 실제 위험은 여기서 막힌다.
      2) 헤더가 없거나(`Transfer-Encoding: chunked`) 값이 거짓일 수 있으므로, 실제로 흘러온
         바이트도 세다가 상한을 넘으면 그 시점에 끊는다.

    한계: 2)는 이미 앱이 본문을 읽기 시작한 뒤라 깔끔한 413 응답을 보장할 수 없다. 그래서
    연결 종료(`http.disconnect`)로 알린다. 정상 클라이언트는 1)에서 걸리므로 실무상 문제없다.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers") or []:
            if name != b"content-length":
                continue
            try:
                declared = int(value)
            except (TypeError, ValueError):
                break  # 형식이 이상하면 아래 실측 카운트에 맡긴다
            if declared > self.max_bytes:
                await self._reject(send)
                return
            break

        received = 0
        max_bytes = self.max_bytes

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    # 본문을 계속 받아 디스크를 채우지 않도록 여기서 끊는다.
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, counting_receive, send)

    async def _reject(self, send):
        limit_mb = self.max_bytes // (1024 * 1024)
        body = json.dumps(
            {"detail": f"요청 본문이 너무 큽니다. (최대 {limit_mb}MB)"},
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
