import unittest

from arithmetic_agent.tools import add, divide, multiply


class ArithmeticToolTests(unittest.TestCase):
    def test_tools_execute_with_structured_arguments(self) -> None:
        self.assertEqual(add.invoke({"a": 3, "b": 4}), 7)
        self.assertEqual(multiply.invoke({"a": 3, "b": 4}), 12)
        self.assertEqual(divide.invoke({"a": 8, "b": 2}), 4.0)

    def test_divide_rejects_zero(self) -> None:
        with self.assertRaises(ValueError):
            divide.invoke({"a": 1, "b": 0})


if __name__ == "__main__":
    unittest.main()
