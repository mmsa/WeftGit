from io import BytesIO
from types import SimpleNamespace
import unittest
from web import ReaderHandler


class Handler:
    read_form = ReaderHandler.read_form
    read_git_body = ReaderHandler.read_git_body
    do_POST = ReaderHandler.do_POST

    def __init__(self, path, body, **headers):
        self.path = path
        self.headers = {"Origin": "http://127.0.0.1:8091", **headers}
        self.rfile = BytesIO(body)
        self.errors = []

    def send_error(self, status, message):
        self.errors.append(status)


class HttpInputTests(unittest.TestCase):
    def test_rejected_identity_requests_never_clear_selected_name(self):
        for body, length, expected in [(b"person=x", "2000", 413), (b"person=x", "10", 400),
                                        (b"other=ok", "8", 400), (b"\xff", "1", 400)]:
            request = Handler("/identity", body, **{"Content-Length":length})
            request.do_POST()
            self.assertEqual(request.errors, [expected])

    def test_session_and_actor_cookie_compatibility(self):
        request = SimpleNamespace(headers={}, server=SimpleNamespace(server_port=8091))
        for kind in ("actor", "session"):
            prefix = "weftgit_" + kind + "_"
            legacy = "freethegit_" + kind + "_8091=old"
            request.headers["Cookie"] = legacy
            self.assertEqual(ReaderHandler.cookie(request, prefix), "old")
            request.headers["Cookie"] = legacy + "; " + prefix + "8091=current"
            self.assertEqual(ReaderHandler.cookie(request, prefix), "current")

    def test_chunked_git_and_size_limit(self):
        good = Handler("/", b"3\r\nabc\r\n4\r\ndefg\r\n0\r\n\r\n", **{"Transfer-Encoding":"chunked"})
        self.assertEqual(good.read_git_body(7), b"abcdefg")
        small = Handler("/", b"8\r\n", **{"Transfer-Encoding":"chunked"})
        self.assertIsNone(small.read_git_body(7))
        self.assertEqual(small.errors, [413])
        incomplete = Handler("/", b"7\r\nabc", **{"Transfer-Encoding":"chunked"})
        self.assertIsNone(incomplete.read_git_body(7))
        self.assertEqual(incomplete.errors, [400])
