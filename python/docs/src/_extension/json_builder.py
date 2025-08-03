import json
import re
from pathlib import Path
from typing import Any, Dict

from docutils import nodes
from sphinx import addnodes
from sphinx.application import Sphinx
from sphinx.builders import Builder
from sphinx.util import logging
from sphinx.writers.text import TextWriter

logger = logging.getLogger(__name__)


class JSONDataBuilder(Builder):
    name = "jsondata"
    format = "json"
    epilog = "JSON data files generated."
    out_suffix = ".json"
    allow_parallel = True

    def init(self) -> None:
        # Phase 1: Data Collection
        self.all_api_classes = []
        self.all_code_blocks = {}  # Using a dict to store source file

    def get_target_uri(self, docname: str, typ: str | None = None) -> str:
        return ""

    def get_outdated_docs(self) -> str | list[str]:
        return "all documents"

    def prepare_writing(self, docnames: set[str]) -> None:
        self.writer = TextWriter(self)

    def write_doc(self, docname: str, doctree: nodes.document) -> None:
        # This method is called for each document. We just collect the data here.

        # Get the full path of the source file to check its extension
        source_filename = self.env.doc2path(docname, base=True)
        source_path = Path(source_filename)

        # Heuristic: API definitions are in files under 'reference/python'
        if "reference/python" in docname:
            api_visitor = APIDataVisitor(doctree)
            doctree.walkabout(api_visitor)
            if api_visitor.data:
                logger.debug(f"Found {len(api_visitor.data)} API classes in {docname}")
                self.all_api_classes.extend(api_visitor.data)

        # Visitor for code blocks (from notebooks or markdown examples)
        if source_path.suffix in [".ipynb", ".md"]:
            code_visitor = CodeBlockVisitor(doctree)
            doctree.walkabout(code_visitor)
            if code_visitor.code_blocks:
                logger.debug(f"Found {len(code_visitor.code_blocks)} code blocks in {docname}")
                self.all_code_blocks[docname] = code_visitor.code_blocks

    def finish(self) -> None:
        # Phase 2: Process and link the collected data
        logger.debug("--- Finish Phase ---")
        logger.debug(f"Total API classes collected: {len(self.all_api_classes)}")
        logger.debug(f"Total files with code blocks: {len(self.all_code_blocks)}")

        # Create a map of short class names to their full data objects
        class_map = {info["class_name"].split(".")[-1]: info for info in self.all_api_classes}

        # Iterate through all collected code blocks from all files
        for docname, code_blocks in self.all_code_blocks.items():
            for code in code_blocks:
                # Check if this code block mentions any of our API classes
                for short_name, class_info in class_map.items():
                    # Use a word boundary regex to avoid partial matches (e.g., 'Agent' in 'AssistantAgent')
                    if re.search(rf"\b{short_name}\b", code):
                        # Link this example to the class, avoiding duplicates
                        if code not in class_info["examples"]:
                            class_info["examples"].append(code)
                            logger.debug(f"Linked example from '{docname}' to class '{short_name}'")

        # The final data is the (now enriched) list of API classes
        output_path = self.outdir / "api_data.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.all_api_classes, f, indent=2)
        logger.debug(f"Wrote final enriched data to {output_path}\n")


class APIDataVisitor(nodes.NodeVisitor):
    def __init__(self, document: nodes.document) -> None:
        super().__init__(document)
        self.data = []
        self.current_class = None

    def visit_desc(self, node: nodes.Node) -> None:
        if node.get("objtype") == "class":
            sig_node = next(iter(node.traverse(addnodes.desc_signature)), None)
            if sig_node:
                class_name = sig_node.get("ids")[0] if sig_node.get("ids") else "UnknownClass"
                signature = re.sub(r"\s+", " ", sig_node.astext()).replace("¶[source]", "").strip()
                content_node = next(iter(node.traverse(addnodes.desc_content)), None)
                description = ""
                if content_node:
                    para_node = next(iter(content_node.traverse(nodes.paragraph)), None)
                    if para_node:
                        description = para_node.astext()
                self.current_class = {
                    "class_name": class_name,
                    "signature": signature,
                    "description": description,
                    "parameters": [],
                    "examples": [],
                }
                self.data.append(self.current_class)

    def depart_desc(self, node: nodes.Node) -> None:
        if node.get("objtype") == "class":
            self.current_class = None

    def visit_field(self, node: nodes.Node) -> None:
        if self.current_class is None or len(node.children) != 2:
            return
        field_name_node, field_body_node = node.children
        if "Parameters" not in field_name_node.astext():
            return
        for list_item in field_body_node.traverse(nodes.list_item):
            para = next(iter(list_item.traverse(nodes.paragraph)), None)
            if not para:
                continue
            strong_tag = next(iter(para.traverse(nodes.strong)), None)
            emphasis_tag = next(iter(para.traverse(nodes.emphasis)), None)
            name = strong_tag.astext() if strong_tag else ""
            type_str = emphasis_tag.astext() if emphasis_tag else "Any"
            if strong_tag:
                strong_tag.parent.remove(strong_tag)
            if emphasis_tag:
                emphasis_tag.parent.remove(emphasis_tag)
            full_text = para.astext()
            desc_start_index = full_text.find("–")
            desc = full_text[desc_start_index + 1 :].strip() if desc_start_index != -1 else ""
            if name:
                self.current_class["parameters"].append({"name": name, "type": type_str, "description": desc})

    def unknown_visit(self, node: nodes.Node) -> None:
        pass

    def unknown_departure(self, node: nodes.Node) -> None:
        pass


class CodeBlockVisitor(nodes.NodeVisitor):
    def __init__(self, document: nodes.document) -> None:
        super().__init__(document)
        self.code_blocks = []

    def visit_literal_block(self, node: nodes.literal_block) -> None:
        self.code_blocks.append(node.astext())

    def unknown_visit(self, node: nodes.Node) -> None:
        pass

    def unknown_departure(self, node: nodes.Node) -> None:
        pass


def setup(app: Sphinx) -> Dict[str, Any]:
    app.add_builder(JSONDataBuilder)
    return {"version": "0.1", "parallel_read_safe": True, "parallel_write_safe": True}
