def greet(name: str = "world") -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "world"
    print(greet(name))
