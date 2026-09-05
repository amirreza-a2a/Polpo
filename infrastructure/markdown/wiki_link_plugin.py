# ============================================================
#  infrastructure/markdown/wiki_link_plugin.py
#  Custom Inline Ruler Plugin for PolpoT Wiki-Link Image Syntax
# ============================================================

import re
from typing import Any, Dict
from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline

# Pattern matches ![[filename]] or ![[filename|params]].
# Prohibits newlines inside the tag syntax.
WIKI_IMAGE_PATTERN = re.compile(r"^!\[\[([^|\]\n]+)(?:\|([^\]\n]*))?\]\]")


def wiki_image_rule(state: StateInline, silent: bool) -> bool:
    """
    Inline ruler rule parsing canonical PolpoT wiki-link image syntax:
      ![[filename]]
      ![[filename|region_id=value]]

    Strictly bounded to canonical syntax. Does not parse arbitrary metadata
    or interpret positional text as alt text.
    Extracts filename and explicit region_id into token.meta.
    Operates at inline tokenization stage before standard 'image' rule,
    guaranteeing that code spans and code fences protect literal content.
    """
    pos = state.pos
    src = state.src
    maximum = state.posMax

    # Fast prefix check: must start with '![['
    if pos + 3 > maximum or src[pos : pos + 3] != "![[":
        return False

    match = WIKI_IMAGE_PATTERN.match(src[pos:maximum])
    if not match:
        return False

    filename = match.group(1).strip()
    if not filename:
        return False

    if silent:
        state.pos += match.end()
        return True

    raw_clause = match.group(2)
    region_id = None

    if raw_clause is not None:
        # A region_id clause must contain exactly one region_id= assignment without extra pipes.
        if "|" in raw_clause:
            region_id = None
        else:
            clause = raw_clause.strip()
            if clause.startswith("region_id="):
                val = clause[len("region_id=") :].strip()
                if val and ("|" not in val):
                    region_id = val

    token = state.push("image", "img", 0)
    token.attrs = {"src": filename, "alt": ""}
    token.content = ""
    token.meta = {
        "wiki_link": True,
        "raw_tag": match.group(0),
        "region_id": region_id,
    }

    state.pos += match.end()
    return True


def wiki_link_plugin(md: MarkdownIt) -> None:
    """
    Registers the PolpoT wiki-link inline rule before standard 'image'.
    """
    md.inline.ruler.before("image", "polpot_wiki_image", wiki_image_rule)
