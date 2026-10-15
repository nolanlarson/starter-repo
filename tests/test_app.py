import unittest

from src.app import greet


class GreetTests(unittest.TestCase):
    def test_default_name(self):
        self.assertEqual(greet(), "Hello, world!")

    def test_custom_name(self):
        self.assertEqual(greet("Alice"), "Hello, Alice!")


if __name__ == "__main__":
    unittest.main()
