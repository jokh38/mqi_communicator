import os
import json
import ast
from pathlib import Path

def get_docstring(node):
    """Extracts the docstring from an AST node."""
    if isinstance(node, ast.Module) and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Str):
        return node.body[0].value.s
    if hasattr(node, 'body') and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Str):
        return node.body[0].value.s
    return ""

def parse_python_file(file_path):
    """Parses a Python file and extracts information about its classes and methods."""
    with open(file_path, "r", encoding="utf-8") as source:
        try:
            tree = ast.parse(source.read(), filename=file_path)
        except Exception as e:
            print(f"Error parsing {file_path}: {e}")
            return None

    file_docstring = get_docstring(tree)
    classes = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_info = {
                "name": node.name,
                "docstring": get_docstring(node),
                "properties": [],  # Placeholder
                "methods": []
            }
            for method_node in node.body:
                if isinstance(method_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_info = {
                        "name": method_node.name,
                        "docstring": get_docstring(method_node)
                    }
                    class_info["methods"].append(method_info)
            classes.append(class_info)

    return {
        "file_name": os.path.basename(file_path),
        "description": file_docstring,
        "classes": classes,
        "refactoring_suggestions": [] # Placeholder
    }

def main():
    """Main function to generate documentation for all Python files."""
    output_dir = Path("./jsons_new")
    output_dir.mkdir(exist_ok=True)

    py_files = []
    # Add main.py
    if os.path.exists("main.py"):
        py_files.append("main.py")

    # Add all .py files in src directory
    for root, _, files in os.walk("src"):
        for file in files:
            if file.endswith(".py"):
                py_files.append(os.path.join(root, file))

    for py_file in py_files:
        file_info = parse_python_file(py_file)
        if file_info:
            json_filename = os.path.basename(py_file).replace(".py", ".json")
            json_filepath = output_dir / json_filename
            with open(json_filepath, "w", encoding="utf-8") as f:
                json.dump(file_info, f, indent=4)
            print(f"Generated documentation for {py_file} at {json_filepath}")

if __name__ == "__main__":
    main()