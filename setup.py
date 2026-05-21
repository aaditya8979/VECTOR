from setuptools import setup, find_packages

setup(
    name            = "tsdc-agent",
    version         = "1.0.0",
    description     = "Task-Scoped Deterministic Context: hallucination-free code modification with 7B models",
    packages        = find_packages(),
    python_requires = ">=3.10",
    install_requires = [
        "tree-sitter>=0.23.0",
        "tree-sitter-python>=0.23.0",
        "networkx>=3.3",
        "watchdog>=4.0.0",
        "llama-cpp-python>=0.2.90",
        "tiktoken>=0.7.0",
        "rich>=13.7.0",
        "click>=8.1.7",
        "pytest>=8.2.0",
        "mypy>=1.10.0",
        "datasets>=2.20.0",
        "requests>=2.32.0",
    ],
    entry_points = {
        "console_scripts": [
            "tsdc = main:cli",
        ],
    },
)