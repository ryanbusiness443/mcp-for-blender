"""The YouTube thumbnail tool: spec building and the code sent to Blender (no Blender needed)."""
import ast
import os

import pytest

from blender_mcp import thumbnails


def test_bundled_fonts_exist():
    for name in thumbnails.FONTS:
        assert os.path.exists(thumbnails.font_path(name)), name
    assert os.path.exists(thumbnails.HERE / "fonts" / "LICENSES.txt")


def test_spec_defaults_and_auto_highlight(tmp_path):
    spec = thumbnails.make_spec("I found the chest", output_path=str(tmp_path / "t.jpg"))
    assert spec["highlight"] == ["CHEST"]  # longest word, later one on a tie
    assert spec["fresh"] is False  # live Blender: never reset the user's file
    assert spec["font"].endswith("LilitaOne-Regular.ttf")
    assert spec["out"].endswith("t.jpg")


def test_spec_rejects_bad_input(tmp_path):
    with pytest.raises(ValueError):
        thumbnails.make_spec("X", preset="nope")
    with pytest.raises(ValueError):
        thumbnails.make_spec("X", face="plaid")
    with pytest.raises(ValueError):
        thumbnails.make_spec("X", background_path=str(tmp_path / "missing.png"))


def test_spec_callouts_and_local_props(tmp_path):
    prop = tmp_path / "thing.glb"
    prop.write_bytes(b"glTF")
    spec = thumbnails.make_spec("ONE HP", arrow_to=[0.8, 0.6], circle_at=[0.2, 0.3], props=[str(prop)],
                                output_path=str(tmp_path / "t.png"))
    assert spec["arrow"]["to"] == [0.8, 0.6]
    assert spec["circle"]["at"] == [0.2, 0.3]
    assert spec["props"] == [{"path": str(prop)}]


def test_build_code_is_valid_python_and_runs_main(tmp_path):
    code = thumbnails.build_code(thumbnails.make_spec("TEST", output_path=str(tmp_path / "t.jpg")))
    tree = ast.parse(code)
    assert isinstance(tree.body[0], ast.Assign) and tree.body[0].targets[0].id == "SPEC"
    # execute_code runs with only `bpy` in the namespace: the script must not need __name__
    assert 'if "SPEC" in globals()' in code
