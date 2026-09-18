import difflib

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple, List
from collections import Counter

class HunkType(Enum):
    CLEAN_UNCHANGED = "clean_unchanged"
    CLEAN_LOCAL = "clean_local"
    CLEAN_REMOTE = "clean_remote"
    CLEAN_SAME = "clean_same"
    CONFLICT = "conflict"

class HunkResolution(Enum):
    UNRESOLVED = "unresolved"
    ACCEPT_LOCAL = "accept_local"
    ACCEPT_REMOTE = "accept_remote"
    ACCEPT_BOTH = "accept_both"
    CUSTOM_TEXT = "custom_text"

@dataclass(frozen=True)
class ConflictHunk:
    base_lines: tuple
    local_lines: tuple
    remote_lines: tuple
    hunk_type: HunkType
    hunk_index: int = -1
    base_line_start: int = 0
    base_line_end: int = 0
    local_line_start: int = 0
    local_line_end: int = 0
    remote_line_start: int = 0
    remote_line_end: int = 0
    ast_node_type: str = ""
    ast_node_id: Optional[str] = None
    ast_label: str = ""

@dataclass(frozen=True)
class ThreeWayMergeResult:
    has_conflicts: bool
    clean_text: Optional[str]
    hunks: tuple
    conflict_count: int
    auto_merged_count: int

def tokenize(text: str) -> Tuple[List[str], List[str], bool]:
    if not text:
        return [], [], False

    raw_lines = text.splitlines(keepends=True)
    lines = []
    endings = []
    for rl in raw_lines:
        if rl.endswith('\r\n'):
            lines.append(rl[:-2])
            endings.append('\r\n')
        elif rl.endswith('\n'):
            lines.append(rl[:-1])
            endings.append('\n')
        elif rl.endswith('\r'):
            lines.append(rl[:-1])
            endings.append('\r')
        else:
            lines.append(rl)
            endings.append('')

    has_trailing = (endings[-1] != '') if endings else False
    return lines, endings, has_trailing

class Change:
    def __init__(self, start, end, source):
        self.start = start
        self.end = end
        self.source = source

def changes_overlap(c1: Change, c2: Change) -> bool:
    s1, e1 = c1.start, c1.end
    s2, e2 = c2.start, c2.end

    if s1 == e1 and s2 == e2:
        return s1 == s2
    elif s1 == e1:
        return s2 < s1 < e2
    elif s2 == e2:
        return s1 < s2 < e1
    else:
        return max(s1, s2) < min(e1, e2)

def map_base_range(opcodes, start_i, end_i, include_insertions=True) -> Tuple[int, int]:
    min_j, max_j = None, None

    def add_j(j1, j2):
        nonlocal min_j, max_j
        if min_j is None or j1 < min_j: min_j = j1
        if max_j is None or j2 > max_j: max_j = j2

    for tag, i1, i2, j1, j2 in opcodes:
        if start_i == end_i:
            if i1 <= start_i <= i2:
                if tag == 'equal':
                    add_j(j1 + (start_i - i1), j1 + (start_i - i1))
                elif tag == 'insert':
                    if include_insertions and i1 == start_i:
                        add_j(j1, j2)
                elif tag in ('replace', 'delete'):
                    if i1 < start_i < i2:
                        add_j(j1, j2)
                    elif start_i == i1:
                        add_j(j1, j1)
                    elif start_i == i2:
                        add_j(j2, j2)
        else:
            if max(i1, start_i) < min(i2, end_i):
                if tag == 'equal':
                    o_s = max(i1, start_i)
                    o_e = min(i2, end_i)
                    add_j(j1 + (o_s - i1), j1 + (o_e - i1))
                else:
                    add_j(j1, j2)
            else:
                if include_insertions and tag == 'insert' and start_i < i1 < end_i:
                    add_j(j1, j2)

    return min_j or 0, max_j or 0

def determine_ending(b_ends, l_ends, r_ends):
    ends = b_ends or l_ends or r_ends
    if not ends:
        return "\n"
    c = Counter(e for e in ends if e)
    if not c:
        return "\n"
    return c.most_common(1)[0][0]

def three_way_merge(base_text: str, local_text: str, remote_text: str) -> ThreeWayMergeResult:
    b_lines, b_ends, b_trail = tokenize(base_text)
    l_lines, l_ends, l_trail = tokenize(local_text)
    r_lines, r_ends, r_trail = tokenize(remote_text)

    sm_l = difflib.SequenceMatcher(None, b_lines, l_lines)
    sm_r = difflib.SequenceMatcher(None, b_lines, r_lines)

    opcodes_L = sm_l.get_opcodes()
    opcodes_R = sm_r.get_opcodes()

    changes = []
    for tag, i1, i2, j1, j2 in opcodes_L:
        if tag != 'equal':
            changes.append(Change(i1, i2, 'L'))
    for tag, i1, i2, j1, j2 in opcodes_R:
        if tag != 'equal':
            changes.append(Change(i1, i2, 'R'))

    blocks = []
    for c in sorted(changes, key=lambda x: (x.start, x.end)):
        overlapping = []
        for b in blocks:
            if any(changes_overlap(x, c) for x in b):
                overlapping.append(b)

        if overlapping:
            new_block = [c]
            for b in overlapping:
                new_block.extend(b)
                blocks.remove(b)
            blocks.append(new_block)
        else:
            blocks.append([c])

    blocks.sort(key=lambda b: min(x.start for x in b))

    hunks = []
    current_idx = 0
    has_conflicts = False
    conflict_count = 0

    def make_hunk(h_type, b_s, b_e):
        nonlocal conflict_count
        l_s, l_e = map_base_range(opcodes_L, b_s, b_e)
        r_s, r_e = map_base_range(opcodes_R, b_s, b_e)
        idx = -1
        if h_type == HunkType.CONFLICT:
            idx = conflict_count
            conflict_count += 1

        return ConflictHunk(
            base_lines=tuple(b_lines[b_s:b_e]),
            local_lines=tuple(l_lines[l_s:l_e]),
            remote_lines=tuple(r_lines[r_s:r_e]),
            hunk_type=h_type,
            hunk_index=idx,
            base_line_start=b_s,
            base_line_end=b_e,
            local_line_start=l_s,
            local_line_end=l_e,
            remote_line_start=r_s,
            remote_line_end=r_e
        )

    for block in blocks:
        min_s = min(x.start for x in block)
        max_e = max(x.end for x in block)

        if min_s > current_idx:
            hunks.append(make_hunk(HunkType.CLEAN_UNCHANGED, current_idx, min_s))

        sources = set(x.source for x in block)
        l_min, l_max = map_base_range(opcodes_L, min_s, max_e)
        r_min, r_max = map_base_range(opcodes_R, min_s, max_e)

        local_lines = tuple(l_lines[l_min:l_max])
        remote_lines = tuple(r_lines[r_min:r_max])

        if sources == {'L'}:
            hunks.append(make_hunk(HunkType.CLEAN_LOCAL, min_s, max_e))
        elif sources == {'R'}:
            hunks.append(make_hunk(HunkType.CLEAN_REMOTE, min_s, max_e))
        else:
            if local_lines == remote_lines:
                hunks.append(make_hunk(HunkType.CLEAN_SAME, min_s, max_e))
            else:
                has_conflicts = True
                hunks.append(make_hunk(HunkType.CONFLICT, min_s, max_e))

        current_idx = max_e

    if current_idx < len(b_lines):
        hunks.append(make_hunk(HunkType.CLEAN_UNCHANGED, current_idx, len(b_lines)))

    clean_text = None
    if not has_conflicts:
        ending = determine_ending(b_ends, l_ends, r_ends)

        final_trail = b_trail
        if hunks:
            last = hunks[-1]
            if last.hunk_type == HunkType.CLEAN_LOCAL:
                final_trail = l_trail
            elif last.hunk_type == HunkType.CLEAN_REMOTE:
                final_trail = r_trail
            elif last.hunk_type == HunkType.CLEAN_SAME:
                final_trail = l_trail

        output_lines = []
        for hunk in hunks:
            if hunk.hunk_type in (HunkType.CLEAN_UNCHANGED, HunkType.CLEAN_SAME):
                output_lines.extend(hunk.local_lines) # or base, same
            elif hunk.hunk_type == HunkType.CLEAN_LOCAL:
                output_lines.extend(hunk.local_lines)
            elif hunk.hunk_type == HunkType.CLEAN_REMOTE:
                output_lines.extend(hunk.remote_lines)

        if output_lines:
            clean_text = ending.join(output_lines)
            if final_trail:
                clean_text += ending
        else:
            clean_text = ""

    return ThreeWayMergeResult(
        has_conflicts=has_conflicts,
        clean_text=clean_text,
        hunks=tuple(hunks),
        conflict_count=conflict_count,
        auto_merged_count=sum(1 for h in hunks if h.hunk_type in (HunkType.CLEAN_LOCAL, HunkType.CLEAN_REMOTE, HunkType.CLEAN_SAME))
    )
