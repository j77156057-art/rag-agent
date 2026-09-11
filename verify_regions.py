"""Offline regression checks for region safety, asset lookup, bug capture and changesets."""
import json
import os
import tempfile

from config import set_runtime
from regions import _normalize_regions, validate_region_config
from tools import dev_asset_get, dev_capture_bug, dev_list_bugs


def main():
    assert not _normalize_regions([{"key": "x", "dir": "../outside"}])
    assert not _normalize_regions([{"key": "x", "dir": "C:/outside"}])
    root = tempfile.mkdtemp(prefix="docmind_regions_")
    os.makedirs(os.path.join(root, "assets", "sprites"), exist_ok=True)
    os.makedirs(os.path.join(root, "bugs"), exist_ok=True)
    with open(os.path.join(root, "assets", "sprites", "hero.png"), "wb") as fh:
        fh.write(b"x")
    with open(os.path.join(root, "assets", "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"hero": {"path": "sprites/hero.png"}}, fh)
    regs = [{"key": "assets", "dir": "assets", "exports": ["manifest.json"]}, {"key": "bugs", "dir": "bugs"}]
    assert validate_region_config(root, regs)["ok"]
    with open(os.path.join(root, "regions.json"), "w", encoding="utf-8") as fh:
        json.dump({"regions": regs}, fh)
    set_runtime("code_root", root)
    assert json.loads(dev_asset_get("asset_id: hero"))["ok"]
    assert "禁止" in dev_asset_get("path: ../outside")
    bug = json.loads(dev_capture_bug("title: test\nerror: boom\ntraceback: tb"))
    assert bug["ok"] and json.loads(dev_list_bugs(""))["bugs"]
    print("OK: region safety / asset lookup / bug capture")


if __name__ == "__main__":
    main()
