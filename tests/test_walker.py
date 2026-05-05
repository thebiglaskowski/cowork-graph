"""Tests for walker.py — uses tmp_path fixture trees."""

from pathlib import Path

from cowork_graph.walker import walk


def make_file(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestWalk:
    def test_yields_md_files(self, tmp_path):
        make_file(tmp_path / "doc.md")
        make_file(tmp_path / "sub" / "child.md")
        results = list(walk(tmp_path))
        rel_paths = {r for _, r in results}
        assert "doc.md" in rel_paths
        assert "sub/child.md" in rel_paths

    def test_skips_non_md_files(self, tmp_path):
        make_file(tmp_path / "notes.txt")
        make_file(tmp_path / "image.png")
        make_file(tmp_path / "doc.md")
        results = list(walk(tmp_path))
        rel_paths = {r for _, r in results}
        assert "notes.txt" not in rel_paths
        assert "image.png" not in rel_paths
        assert "doc.md" in rel_paths

    def test_skips_git_dir(self, tmp_path):
        make_file(tmp_path / ".git" / "COMMIT_EDITMSG")
        make_file(tmp_path / ".git" / "notes.md")
        make_file(tmp_path / "doc.md")
        results = list(walk(tmp_path))
        rel_paths = {r for _, r in results}
        assert not any(".git" in r for r in rel_paths)
        assert "doc.md" in rel_paths

    def test_skips_obsidian_dir(self, tmp_path):
        make_file(tmp_path / ".obsidian" / "config.md")
        make_file(tmp_path / "doc.md")
        results = list(walk(tmp_path))
        rel_paths = {r for _, r in results}
        assert not any(".obsidian" in r for r in rel_paths)

    def test_skips_archive_dir_at_root(self, tmp_path):
        make_file(tmp_path / "_archive" / "old.md")
        make_file(tmp_path / "doc.md")
        results = list(walk(tmp_path))
        rel_paths = {r for _, r in results}
        assert not any("_archive" in r for r in rel_paths)
        assert "doc.md" in rel_paths

    def test_skips_archive_dir_nested(self, tmp_path):
        # _archive anywhere in the path must be skipped
        make_file(tmp_path / "autoscriptstudio" / "_archive" / "draft.md")
        make_file(tmp_path / "autoscriptstudio" / "current.md")
        results = list(walk(tmp_path))
        rel_paths = {r for _, r in results}
        assert not any("_archive" in r for r in rel_paths)
        assert "autoscriptstudio/current.md" in rel_paths

    def test_skips_projects_symlink(self, tmp_path):
        real_dir = tmp_path / "real_dir"
        make_file(real_dir / "linked.md")
        projects_link = tmp_path / ".projects"
        projects_link.symlink_to(real_dir)
        make_file(tmp_path / "doc.md")
        results = list(walk(tmp_path))
        rel_paths = {r for _, r in results}
        assert not any(".projects" in r for r in rel_paths)
        assert "doc.md" in rel_paths

    def test_skips_venv_dir(self, tmp_path):
        make_file(tmp_path / ".venv" / "lib" / "site.md")
        make_file(tmp_path / "doc.md")
        results = list(walk(tmp_path))
        rel_paths = {r for _, r in results}
        assert not any(".venv" in r for r in rel_paths)

    def test_yields_absolute_and_relative(self, tmp_path):
        make_file(tmp_path / "sub" / "note.md")
        results = list(walk(tmp_path))
        assert len(results) == 1
        abs_path, rel = results[0]
        assert abs_path.is_absolute()
        assert rel == "sub/note.md"

    def test_empty_directory_returns_nothing(self, tmp_path):
        assert list(walk(tmp_path)) == []
