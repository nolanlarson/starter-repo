import unittest
from unittest.mock import call, patch

from app.console import run_chat


class ConsoleChatTests(unittest.TestCase):
    @patch("app.console.stream_prompt", side_effect=["Hello!", "You said hello."])
    @patch("builtins.input", side_effect=["Hello", "What did I say?", "/quit"])
    def test_chat_sends_prior_turns_until_quit(self, _input, stream_prompt):
        run_chat("http://localhost:8000/stream")

        self.assertEqual(
            stream_prompt.call_args_list,
            [
                call("http://localhost:8000/stream", "Hello"),
                call(
                    "http://localhost:8000/stream",
                    "Continue this conversation using the earlier turns as context.\n\n"
                    "User: Hello\nAssistant: Hello!\nUser: What did I say?\nAssistant:",
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()