"""R4 资产与模板单测（v1.1 打磨 §4，S9 归属）+ R1 create-skill 资产。

覆盖：
- models.toml.example 可解析、两档案字段齐全、无 api_key 明文（FR-33）；
- create-skill/SKILL.md frontmatter 可解析、name 与目录名一致；
- ensure_models_toml：缺失时生成到指定路径、已存在绝不覆盖、模板损坏静默回退；
- 生成后 load_registry 解析出两档案且默认取首段。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from glaucous.extensions.skills import _parse_frontmatter
from glaucous.llm import registry


def asset_path(rel: str) -> Path:
    from importlib.resources import files

    return Path(str(files("glaucous").joinpath(rel)))


class TestModelsTemplate:
    def test_template_parses_two_profiles_without_plaintext_key(self) -> None:
        data = tomllib.loads(asset_path("assets/models.toml.example").read_text(encoding="utf-8"))
        models = data["models"]
        assert set(models) == {"deepseek-v4-flash", "deepseek-v4-pro"}
        for name, spec in models.items():
            assert spec["base_url"].startswith("https://")
            assert spec["model"] == name
            assert spec["api_key_env"] == "GLAUCOUS_API_KEY"
            assert "api_key" not in spec, f"档案 [{name}] 不得含密钥明文（FR-33）"

    def test_template_first_profile_is_default(self) -> None:
        """默认档案 = 首段：与 GLAUCOUS_DEFAULT_MODEL 缺省语义兼容（§4.2 验收）。"""
        text = asset_path("assets/models.toml.example").read_text(encoding="utf-8")
        assert text.index("deepseek-v4-flash") < text.index("deepseek-v4-pro")


class TestCreateSkillAsset:
    def test_frontmatter_parses_and_name_matches_dir(self) -> None:
        path = asset_path("assets/skills/create-skill/SKILL.md")
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        assert meta["name"] == "create-skill"
        assert "技能" in meta["description"]  # description 写明触发场景句
        assert body.strip()


@pytest.fixture()
def toml_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 models_toml_path 重定向到 tmp 目录（不动真实用户主目录）。"""
    target = tmp_path / ".glaucous" / "models.toml"
    monkeypatch.setattr(registry, "models_toml_path", lambda: target)
    return target


class TestEnsureModelsToml:
    def test_generates_when_missing(self, toml_target: Path) -> None:
        assert not toml_target.exists()
        registry.ensure_models_toml()
        assert toml_target.exists()
        assert toml_target.read_text(encoding="utf-8") == asset_path(
            "assets/models.toml.example"
        ).read_text(encoding="utf-8")

    def test_never_overwrites_existing(self, toml_target: Path) -> None:
        toml_target.parent.mkdir(parents=True, exist_ok=True)
        sentinel = "# 用户已有配置，逐字节不变"
        toml_target.write_text(sentinel, encoding="utf-8")
        registry.ensure_models_toml()
        assert toml_target.read_text(encoding="utf-8") == sentinel

    def test_template_broken_falls_back_silently(
        self, toml_target: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """模板读取失败 → 静默返回，不生成、不抛错，由 env 兜底接管（§4.2）。"""

        def broken(_package):
            raise OSError("打包形态异常")

        monkeypatch.setattr("importlib.resources.files", broken)
        registry.ensure_models_toml()  # 不抛错
        assert not toml_target.exists()
        entries, default = registry.load_registry(env={})
        assert default == registry.ENV_PROFILE_NAME  # env 单档案兜底

    def test_load_registry_parses_generated_two_profiles(self, toml_target: Path) -> None:
        registry.ensure_models_toml()
        entries, default = registry.load_registry(env={})
        assert set(entries) == {"deepseek-v4-flash", "deepseek-v4-pro"}
        assert default == "deepseek-v4-flash"
        assert entries["deepseek-v4-pro"].model == "deepseek-v4-pro"
