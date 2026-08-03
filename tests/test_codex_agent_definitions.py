from pathlib import Path
import tomllib


def test_codex_roles_match_the_existing_specialist_inventory():
    codex = {path.stem for path in Path(".codex/agents").glob("*.toml")}
    claude = {path.stem for path in Path(".claude/agents").glob("*.md")}
    assert codex == claude


def test_codex_agent_toml_is_complete_and_cloud_portable():
    for path in Path(".codex/agents").glob("*.toml"):
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        assert config["name"] == path.stem
        assert config["description"].strip()
        assert config["developer_instructions"].strip()
        assert "/Users/" not in config["developer_instructions"]


def test_dashboard_skills_have_codex_skill_front_matter():
    skills = sorted(Path(".agents/skills").glob("*/SKILL.md"))
    assert len(skills) == 5
    for path in skills:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "\nname:" in text
        assert "\ndescription:" in text
