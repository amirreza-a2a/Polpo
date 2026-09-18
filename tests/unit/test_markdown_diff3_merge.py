import pytest
from core.markdown.merge import (
    three_way_merge,
    HunkType,
    HunkResolution,
    ConflictHunk,
    ThreeWayMergeResult,
)

def test_t_merge_01_clean_local_only_change():
    base = "line 1\nline 2\nline 3\n"
    local = "line 1\nline 2 modified\nline 3\n"
    remote = "line 1\nline 2\nline 3\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is False
    assert result.clean_text == local

def test_t_merge_02_clean_remote_only_change():
    base = "line 1\nline 2\nline 3\n"
    local = "line 1\nline 2\nline 3\n"
    remote = "line 1\nline 2 modified\nline 3\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is False
    assert result.clean_text == remote

def test_t_merge_03_clean_disjoint_changes():
    base = "line 1\nline 2\nline 3\nline 4\nline 5\n"
    local = "line 1 modified\nline 2\nline 3\nline 4\nline 5\n"
    remote = "line 1\nline 2\nline 3\nline 4\nline 5 modified\n"
    expected = "line 1 modified\nline 2\nline 3\nline 4\nline 5 modified\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is False
    assert result.clean_text == expected

def test_t_merge_04_identical_concurrent_change():
    base = "line 1\nline 2\nline 3\n"
    local = "line 1\nline 2 modified\nline 3\n"
    remote = "line 1\nline 2 modified\nline 3\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is False
    assert result.clean_text == local

def test_t_merge_05_overlapping_replace_replace():
    base = "line 1\nline 2\nline 3\n"
    local = "line 1\nline 2 local\nline 3\n"
    remote = "line 1\nline 2 remote\nline 3\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is True
    assert result.conflict_count == 1
    assert result.clean_text is None
    conflict = [h for h in result.hunks if h.hunk_type == HunkType.CONFLICT][0]
    assert conflict.base_lines == ("line 2",)
    assert conflict.local_lines == ("line 2 local",)
    assert conflict.remote_lines == ("line 2 remote",)

def test_t_merge_06_delete_vs_modify():
    base = "line 1\nline 2\nline 3\n"
    local = "line 1\nline 3\n"
    remote = "line 1\nline 2 remote\nline 3\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is True
    assert result.conflict_count == 1
    conflict = [h for h in result.hunks if h.hunk_type == HunkType.CONFLICT][0]
    assert conflict.base_lines == ("line 2",)
    assert conflict.local_lines == ()
    assert conflict.remote_lines == ("line 2 remote",)

def test_t_merge_07_modify_vs_delete():
    base = "line 1\nline 2\nline 3\n"
    local = "line 1\nline 2 local\nline 3\n"
    remote = "line 1\nline 3\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is True
    assert result.conflict_count == 1
    conflict = [h for h in result.hunks if h.hunk_type == HunkType.CONFLICT][0]
    assert conflict.base_lines == ("line 2",)
    assert conflict.local_lines == ("line 2 local",)
    assert conflict.remote_lines == ()

def test_t_merge_08_differing_insertions_at_identical_offset():
    base = "line 1\nline 2\n"
    local = "line 1\nlocal insert\nline 2\n"
    remote = "line 1\nremote insert\nline 2\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is True
    assert result.conflict_count == 1
    conflict = [h for h in result.hunks if h.hunk_type == HunkType.CONFLICT][0]
    assert conflict.base_lines == ()
    assert conflict.local_lines == ("local insert",)
    assert conflict.remote_lines == ("remote insert",)

def test_t_merge_09_adjacent_non_overlapping_edits():
    base = "line 1\nline 2\nline 3\n"
    local = "line 1\nlocal insert\nline 2\nline 3\n"
    remote = "line 1\nline 2\nremote insert\nline 3\n"
    expected = "line 1\nlocal insert\nline 2\nremote insert\nline 3\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is False
    assert result.clean_text == expected

def test_t_merge_10_repeated_blank_lines_and_separators():
    base = "a\n\n\n---\n\n\nb\n"
    local = "a\n\n\n---\n\n\nb local\n"
    remote = "a remote\n\n\n---\n\n\nb\n"
    expected = "a remote\n\n\n---\n\n\nb local\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is False
    assert result.clean_text == expected

def test_t_merge_11_whitespace_and_indentation_preserved():
    base = "    indented line\n\t\ttabbed line\n"
    local = "    indented line modified\n\t\ttabbed line\n"
    remote = "    indented line\n\t\ttabbed line modified\n"
    expected = "    indented line modified\n\t\ttabbed line modified\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is False
    assert result.clean_text == expected

def test_t_merge_12_crlf_vs_lf_line_endings():
    base = "line 1\r\nline 2\r\n"
    local = "line 1\nline 2\nline 3\n"
    remote = "line 1\r\nline 2 mod\r\n"
    expected = "line 1\r\nline 2 mod\r\nline 3\r\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is False
    assert result.clean_text == expected

def test_t_merge_13_trailing_newline_presence():
    base = "line 1\nline 2"
    local = "line 1\nline 2 mod"
    remote = "line 1\nline 2"
    expected = "line 1\nline 2 mod"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is False
    assert result.clean_text == expected

def test_t_merge_14_eof_insertion_without_trailing_newline():
    base = "line 1"
    local = "line 1\nlocal insert"
    remote = "line 1\nremote insert"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is True
    assert result.conflict_count == 1
    conflict = [h for h in result.hunks if h.hunk_type == HunkType.CONFLICT][0]
    assert conflict.local_lines == ("local insert",)
    assert conflict.remote_lines == ("remote insert",)

def test_t_merge_15_empty_document():
    result = three_way_merge("", "", "")
    assert result.has_conflicts is False
    assert result.clean_text == ""

    result2 = three_way_merge("", "local\n", "")
    assert result2.has_conflicts is False
    assert result2.clean_text == "local\n"

    result3 = three_way_merge("", "local\n", "remote\n")
    assert result3.has_conflicts is True

def test_t_merge_16_markdown_region_token_updates():
    base = "![[crop_1|region_id=A]]\n"
    local = "![[crop_1|region_id=B]]\n"
    remote = "![[crop_1|region_id=C]]\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is True
    
    local2 = "![[crop_2|region_id=A]]\n"
    remote2 = "![[crop_1|region_id=C]]\n"
    
    result2 = three_way_merge(base, local2, remote2)
    assert result2.has_conflicts is True

def test_t_merge_17_markdown_text_with_conflict_markers():
    base = "some text\n<<<<<<<\n=======\n>>>>>>>\n"
    local = "some text local\n<<<<<<<\n=======\n>>>>>>>\n"
    remote = "some text remote\n<<<<<<<\n=======\n>>>>>>>\n"
    
    result = three_way_merge(base, local, remote)
    assert result.has_conflicts is True
    # Ensure it's treated as a conflict but not breaking the merge due to parsing
    # Since our merge engine doesn't parse text markers, it just compares lines,
    # it shouldn't raise any special error.
    
    # What if it's a clean merge but contains these markers?
    base3 = "some text\n<<<<<<<\n"
    local3 = "some text local\n<<<<<<<\n"
    remote3 = "some text\n<<<<<<<\n"
    result3 = three_way_merge(base3, local3, remote3)
    assert result3.has_conflicts is False
    assert result3.clean_text == "some text local\n<<<<<<<\n"
