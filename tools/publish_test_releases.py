#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Upload testing-channel zips as immutable GitHub Release assets.

The testing datadir is the releases/download prefix, so Kodi requests
"<datadir><id>/<id>-<version>.zip". The tag is the add-on id and the asset
name is the zip file. An asset that is already present is left in place:
replacing it resets download_count. This does not download the asset.
"""

import argparse
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

from build_repo import (
    RELEASE_DOWNLOAD_DATADIR,
    ROOT,
    TESTING_DIR,
    TESTING_PLUGIN,
    TESTING_REPO,
    addon_blocks,
    fail,
    release_asset_from_datadir,
    repository_dirs,
)

NOTES = (
    "Test-feed packages for Kodi. Each asset is one add-on version. "
    "Do not replace or delete an asset; that resets its download_count."
)
REQUIRED = (TESTING_PLUGIN, TESTING_REPO)


def upload_action(assets, name, size, sha256):
    """Return upload, skip, or mismatch for one release asset.

    mismatch means the name is already published with different bytes.
    Those bytes stay. A new version needs a new filename.
    """
    for asset in assets:
        if asset.get("name") != name:
            continue
        if int(asset.get("size", -1)) != size:
            return "mismatch"
        digest = asset.get("digest") or ""
        if digest.lower().startswith("sha256:"):
            published = digest.split(":", 1)[1]
            if published.lower() != sha256.lower():
                return "mismatch"
        return "skip"
    return "upload"


def check_upload_rules():
    name = "plugin.video.primeinterface-1.2.3.zip"
    digest = "sha256:" + "ab" * 32
    if upload_action([], name, 10, "ab" * 32) != "upload":
        fail("missing asset should upload")
    if upload_action([{"name": "other.zip", "size": 10}], name, 10, "ab" * 32) != "upload":
        fail("a different asset name should upload")
    same = [{"name": name, "size": 10, "digest": digest}]
    if upload_action(same, name, 10, "ab" * 32) != "skip":
        fail("matching asset should be kept")
    if upload_action([{"name": name, "size": 11, "digest": digest}], name, 10, "ab" * 32) != "mismatch":
        fail("different size should not replace the asset")
    other = "sha256:" + "cd" * 32
    if upload_action([{"name": name, "size": 10, "digest": other}], name, 10, "ab" * 32) != "mismatch":
        fail("different sha256 should not replace the asset")
    if upload_action([{"name": name, "size": 10}], name, 10, "ab" * 32) != "skip":
        fail("size match without a digest should be kept")
    print("upload rules ok")


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def testing_addons():
    xml_path = ROOT / TESTING_DIR / "addons.xml"
    if not xml_path.is_file():
        fail("missing %s" % xml_path)
    text = xml_path.read_text(encoding="utf-8")
    blocks = addon_blocks(text)
    if TESTING_REPO not in blocks:
        fail("testing addons.xml has no %s" % TESTING_REPO)
    # The on-disk index is the document Kodi fetches. Read the datadir from
    # that element, not from a copy of the source addon.xml.
    dirs = repository_dirs(blocks[TESTING_REPO])
    root = ET.parse(xml_path).getroot()
    if not dirs or dirs["datadir"] != RELEASE_DOWNLOAD_DATADIR:
        fail("testing addons.xml datadir is not the release download prefix")
    found = []
    for addon in root.findall("addon"):
        addon_id = addon.attrib["id"]
        version = addon.attrib["version"]
        filename = "%s-%s.zip" % (addon_id, version)
        local = ROOT / TESTING_DIR / addon_id / filename
        if not local.is_file():
            fail("missing Pages fallback zip %s" % local)
        url, tag, asset_name = release_asset_from_datadir(dirs["datadir"], addon_id, version)
        if asset_name != filename:
            fail("asset name %s does not match %s" % (asset_name, filename))
        found.append((addon_id, url, tag, local))
    ids = [item[0] for item in found]
    for addon_id in REQUIRED:
        if addon_id not in ids:
            fail("testing addons.xml is missing %s" % addon_id)
    return found


def release_slug(url):
    prefix = "https://github.com/"
    marker = "/releases/download/"
    if not url.startswith(prefix) or marker not in url:
        fail("not a release download URL: %s" % url)
    owner_repo = url[len(prefix):].split(marker, 1)[0]
    if owner_repo.count("/") != 1:
        fail("release URL has no owner/repo: %s" % url)
    return owner_repo


def existing_assets(slug, tag):
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", slug, "--json", "assets"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        payload = json.loads(result.stdout)
        return payload.get("assets", [])
    detail = (result.stderr or result.stdout or "").lower()
    if "not found" in detail or "release not found" in detail or "not exist" in detail:
        return None
    fail("could not read release %s: %s" % (tag, (result.stderr or result.stdout or "").strip()))


def ensure_release(slug, tag):
    subprocess.run(
        [
            "gh", "release", "create", tag,
            "--repo", slug,
            "--prerelease",
            "--title", "Test feed %s" % tag,
            "--notes", NOTES,
        ],
        check=True,
    )


def upload_asset(slug, tag, path):
    subprocess.run(
        ["gh", "release", "upload", tag, str(path), "--repo", slug],
        check=True,
    )


def publish(dry_run):
    planned = testing_addons()
    slug = release_slug(planned[0][1])
    for addon_id, url, tag, path in planned:
        if release_slug(url) != slug:
            fail("%s release URL is not under %s" % (addon_id, slug))
        size = path.stat().st_size
        sha256 = file_sha256(path)
        if dry_run:
            print("dry-run %s %s (%s bytes) from %s" % (tag, path.name, size, path))
            print("dry-run url %s" % url)
            continue
        assets = existing_assets(slug, tag)
        if assets is None:
            ensure_release(slug, tag)
            assets = []
        action = upload_action(assets, path.name, size, sha256)
        if action == "skip":
            print("kept %s/%s (%s bytes); download_count unchanged" % (tag, path.name, size))
        elif action == "mismatch":
            fail(
                "%s/%s is already published with different bytes. "
                "Leaving it in place so download_count is kept. "
                "Publish a new add-on version instead of replacing this asset."
                % (tag, path.name)
            )
        else:
            upload_asset(slug, tag, path)
            print("uploaded %s/%s (%s bytes)" % (tag, path.name, size))
        print("kodi url %s" % url)
        print(
            "counts https://api.github.com/repos/%s/releases/tags/%s"
            % (slug, tag)
        )


def main():
    parser = argparse.ArgumentParser(description="Publish testing zips as GitHub Release assets.")
    parser.add_argument("--dry-run", action="store_true", help="Print the release URLs and upload nothing.")
    parser.add_argument("--self-check", action="store_true", help="Check the skip-if-present rules and exit.")
    args = parser.parse_args()
    if args.self_check:
        check_upload_rules()
        return
    publish(args.dry_run)


if __name__ == "__main__":
    main()
