import io
import json
import unittest
from unittest.mock import Mock, patch

from app.agent import MAX_PROMPT_CHARS
from app.server import AgentRequestHandler


class AgentServerTests(unittest.TestCase):
    def test_rejects_oversized_prompt_before_starting_agent(self):
        body = json.dumps({"prompt": "x" * (MAX_PROMPT_CHARS + 1)}).encode()
        handler = AgentRequestHandler.__new__(AgentRequestHandler)
        handler.path = "/stream"
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()

        with patch.object(handler, "_stream_events") as stream_events:
            handler.do_POST()

        handler.send_response.assert_called_once_with(413)
        self.assertIn("character limit", handler.wfile.getvalue().decode())
        stream_events.assert_not_called()


if __name__ == "__main__":
    unittest.main()