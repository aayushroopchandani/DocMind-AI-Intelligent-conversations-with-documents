# this file is fo pdf who have outline 
import fitz  # from pymupdf
from .utils.searching_for_nodes import normalize_title

def get_raw_outline(pdf_path):
    doc = fitz.open(pdf_path)

    doc = fitz.open(pdf_path)
    last_page = doc.page_count

    toc = doc.get_toc()   # -> [[level, title, page], ...]
    doc.close()
    return toc,last_page


def build_tree(toc, last_page):
    nodes = []
    for i, (level, title, start) in enumerate(toc):
        # find where this node ends: the next entry at same or higher level
        end = last_page
        for (next_level, _, next_start) in toc[i + 1:]:
            if next_level <= level:
                end = next_start - 1
                break
        nodes.append({
            "node_id": f"node_{i}",
            "title": title,
            "level": level,
            "page_start": start,
            "page_end": end,
            "parent_id": None,   # filled next
            "normalized_title": normalize_title(title),
        })

    # parent = the most recent earlier node one level shallower
    for i, node in enumerate(nodes):
        for prev in reversed(nodes[:i]):
            if prev["level"] == node["level"] - 1:
                node["parent_id"] = prev["node_id"]
                break
    return nodes


def build_tree_from_pdf(pdf_path):
    toc,last_page = get_raw_outline(pdf_path)
    return build_tree(toc,last_page)


# for chunks def find_node_id(page, nodes):
def find_node_id(page, nodes):
    match = None
    for node in nodes:
        if node["page_start"] <= page <= node["page_end"]:
            # keep the deepest (most specific) match
            if match is None or node["level"] > match["level"]:
                match = node
    return match["node_id"] if match else None




