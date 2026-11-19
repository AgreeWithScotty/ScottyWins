#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild the ScottyWins Kodi repository from the two source checkouts.

The script is deterministic: zip entries use a fixed timestamp, mode, and
file order, and copied release zips are written through unchanged. Only the
latest version of each add-on is kept.

Kodi's HTTP directory parser (HTTPDirectory.cpp) keeps an <a href> only when
the link text equals the href, and it joins that href onto the source path
with plain concatenation. A source without a trailing slash would glue the
zip name onto "ScottyWins". The index therefore uses one leading slash in
the href so both of these list the zip:

  https://agreewithscotty.github.io/ScottyWins
  https://agreewithscotty.github.io/ScottyWins/
"""

import argparse
import hashlib
import io
import os
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = "https://agreewithscotty.github.io/ScottyWins"
PRIME_ID = "plugin.video.primeinterface"
SMOOTH_ID = "plugin.video.smoothflix"
REPO_ADDONS = (
    "repository.primescene",
    "repository.scottywins",
    "repository.smoothflix",
)
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIXES = (".zip", ".pyc", ".pyo", ".tmp")
# Same expression Kodi compiles in CHTTPDirectory::GetDirectory.
KODI_LINK = re.compile(
    r'<a href="([^"]*)"[^>]*>\s*(.*?)\s*</a>(.+?)(?=<a|</tr|$)',
    re.IGNORECASE,
)


def fail(message):
    print("error: %s" % message, file=sys.stderr)
    raise SystemExit(1)


def addon_fragment(text):
    """Return the <addon> element, without the XML declaration."""
    text = text.strip().lstrip("\ufeff")
    if text.startswith("<?xml"):
        text = text.split("?>", 1)[1].strip()
    if not text.startswith("<addon "):
        fail("addon.xml does not start with an addon element")
    return text


def addon_id_version(text):
    root = ET.fromstring(addon_fragment(text).encode("utf-8"))
    return root.attrib["id"], root.attrib["version"]


def read_zip_addon_xml(data, addon_id):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = "%s/addon.xml" % addon_id
        try:
            raw = archive.read(name)
        except KeyError:
            fail("%s is missing from the zip" % name)
    return raw.decode("utf-8")


def zip_directory(addon_dir, dest):
    """Write a Kodi add-on zip. The archive root is the add-on id."""
    addon_id = addon_dir.name
    files = []
    for folder, dirs, names in os.walk(addon_dir):
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
        for name in names:
            if name.endswith(SKIP_SUFFIXES):
                continue
            full = Path(folder) / name
            if not full.is_file():
                continue
            rel = full.relative_to(addon_dir.parent).as_posix()
            files.append((rel, full))
    files.sort()
    if "%s/addon.xml" % addon_id not in [rel for rel, _full in files]:
        fail("%s has no addon.xml" % addon_dir)

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w") as archive:
        for rel, full in files:
            info = zipfile.ZipInfo(filename=rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o644 << 16)
            info.flag_bits = 0
            archive.writestr(info, full.read_bytes(), compresslevel=9)
    os.replace(tmp, dest)


def version_from_addons_xml(path, addon_id):
    if not path.is_file():
        fail("missing %s" % path)
    root = ET.parse(path).getroot()
    for element in root.findall("addon"):
        if element.attrib.get("id") == addon_id:
            return element.attrib["version"]
    fail("%s is not listed in %s" % (addon_id, path))


def load_prime_zip(repo):
    version = version_from_addons_xml(repo / "addons.xml", PRIME_ID)
    filename = "%s-%s.zip" % (PRIME_ID, version)
    local = repo / "packages" / PRIME_ID / filename
    if local.is_file():
        data = local.read_bytes()
        origin = str(local)
    else:
        url = (
            "https://raw.githubusercontent.com/AgreeWithScotty/PrimeScene/main/"
            "packages/%s/%s" % (PRIME_ID, filename)
        )
        request = urllib.request.Request(url, headers={"User-Agent": "scottywins-build"})
        with urllib.request.urlopen(request, timeout=180) as response:
            data = response.read()
        origin = url
    text = read_zip_addon_xml(data, PRIME_ID)
    found_id, found_version = addon_id_version(text)
    if found_id != PRIME_ID or found_version != version:
        fail("Prime Scene zip %s is %s %s" % (origin, found_id, found_version))
    return filename, data, text


def smooth_source(repo):
    addon_dir = repo / SMOOTH_ID
    addon_xml = addon_dir / "addon.xml"
    if not addon_xml.is_file():
        fail("missing %s" % addon_xml)
    text = addon_xml.read_text(encoding="utf-8")
    found_id, version = addon_id_version(text)
    if found_id != SMOOTH_ID:
        fail("%s has id %s" % (addon_dir, found_id))
    return addon_dir, version, text


def write_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return
    path.write_bytes(data)


def remove_stale_zips(folder, addon_id, filename):
    if not folder.is_dir():
        return
    for path in folder.glob("%s-*.zip" % addon_id):
        if path.name != filename:
            path.unlink()


def kodi_link_targets(html):
    """Return hrefs Kodi would keep, after its one-character slash strip."""
    found = []
    for match in KODI_LINK.finditer(html):
        href = match.group(1)
        name = match.group(2).strip()
        if href.startswith("/"):
            href = href[1:]
        link = href.split("?", 1)[0].split("#", 1)[0]
        if link.endswith("/"):
            link = link[:-1]
        if link in ("", ".."):
            continue
        if name != link and not (":" in name and "./" + name == link):
            continue
        found.append(href)
    return found


def write_index(zip_name, size_bytes):
    kilobytes = max(1, size_bytes // 1024)
    # The anchor is the last line. Kodi's "." does not cross a newline, and
    # the match needs at least one character after </a> before the end.
    html = (
        "<!doctype html>\n"
        '<html lang="en">\n'
        '<head><meta charset="utf-8"><title>ScottyWins Repository</title></head>\n'
        "<body>\n"
        "<h1>ScottyWins Repository</h1>\n"
        '<a href="//%s">/%s</a> %dK\n'
    ) % (zip_name, zip_name, kilobytes)
    targets = kodi_link_targets(html)
    if targets != ["/" + zip_name]:
        fail("index.html is not readable by Kodi's HTTP directory parser: %r" % targets)
    bare = "ScottyWins" + targets[0]
    slashed = "ScottyWins/" + targets[0]
    if bare != "ScottyWins/" + zip_name or slashed != "ScottyWins//" + zip_name:
        fail("index.html join check failed: %s / %s" % (bare, slashed))
    write_bytes(ROOT / "index.html", html.encode("utf-8"))


def replace_addon_block(text, addon_id, fragment):
    marker = '<addon id="%s"' % addon_id
    start = text.find(marker)
    if start < 0:
        fail("addons.xml has no %s" % addon_id)
    end = text.find("</addon>", start)
    if end < 0:
        fail("addons.xml has an unclosed %s" % addon_id)
    end += len("</addon>")
    line_start = text.rfind("\n", 0, start) + 1
    indent = text[line_start:start]
    if indent.strip():
        indent = ""
    block = "\n".join(indent + line if line else line for line in fragment.split("\n"))
    return text[:start] + block + text[end:]


def write_legacy_index(checkout, addon_id, fragment):
    xml_path = checkout / "addons.xml"
    original = xml_path.read_text(encoding="utf-8")
    updated = replace_addon_block(original, addon_id, fragment)
    # The plugin listing has to stay byte-for-byte so this commit only moves
    # the repository add-on.
    plugin_id = PRIME_ID if addon_id == "repository.primescene" else SMOOTH_ID
    plugin_marker = '<addon id="%s"' % plugin_id
    old_plugin = original[original.find(plugin_marker):original.find("</addon>", original.find(plugin_marker)) + len("</addon>")]
    if old_plugin not in updated:
        fail("legacy update rewrote %s" % plugin_id)
    data = updated.encode("utf-8")
    xml_path.write_bytes(data)
    digest = hashlib.md5(data).hexdigest() + "\n"
    (checkout / "addons.xml.md5").write_text(digest, encoding="utf-8")
    print("legacy %s md5 %s" % (checkout.name, digest.strip()))


def update_legacy(primescene, smoothflix, built):
    prime_id = "repository.primescene"
    smooth_id = "repository.smoothflix"
    prime_zip = built[prime_id]
    smooth_zip = built[smooth_id]
    shutil.copyfile(prime_zip, primescene / "packages" / prime_id / prime_zip.name)
    shutil.copyfile(ROOT / prime_id / "addon.xml", primescene / "packages" / prime_id / "addon.xml")
    write_legacy_index(primescene, prime_id, addon_fragment((ROOT / prime_id / "addon.xml").read_text(encoding="utf-8")))

    shutil.copyfile(smooth_zip, smoothflix / smooth_id / smooth_zip.name)
    for name in ("addon.xml", "changelog.txt"):
        shutil.copyfile(ROOT / smooth_id / name, smoothflix / smooth_id / name)
    write_legacy_index(smoothflix, smooth_id, addon_fragment((ROOT / smooth_id / "addon.xml").read_text(encoding="utf-8")))


def main():
    parser = argparse.ArgumentParser(description="Rebuild the ScottyWins Kodi repository.")
    parser.add_argument("--primescene", required=True, type=Path)
    parser.add_argument("--smoothflix", required=True, type=Path)
    parser.add_argument(
        "--update-legacy",
        action="store_true",
        help="Also write the bumped repository add-ons into the source checkouts.",
    )
    args = parser.parse_args()
    primescene = args.primescene.resolve()
    smoothflix = args.smoothflix.resolve()

    fragments = {}

    prime_filename, prime_data, prime_xml = load_prime_zip(primescene)
    prime_dir = ROOT / PRIME_ID
    prime_dir.mkdir(parents=True, exist_ok=True)
    write_bytes(prime_dir / prime_filename, prime_data)
    remove_stale_zips(prime_dir, PRIME_ID, prime_filename)
    fragments[PRIME_ID] = addon_fragment(prime_xml)
    print("plugin %s %s (%s bytes, copied)" % (PRIME_ID, prime_filename, len(prime_data)))

    smooth_dir, smooth_version, smooth_xml = smooth_source(smoothflix)
    smooth_filename = "%s-%s.zip" % (SMOOTH_ID, smooth_version)
    dest_dir = ROOT / SMOOTH_ID
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Zip from the source checkout straight into this repository.
    zip_directory(smooth_dir, dest_dir / smooth_filename)
    remove_stale_zips(dest_dir, SMOOTH_ID, smooth_filename)
    packed = read_zip_addon_xml((dest_dir / smooth_filename).read_bytes(), SMOOTH_ID)
    if addon_id_version(packed) != (SMOOTH_ID, smooth_version):
        fail("SmoothFlix zip does not match its addon.xml")
    fragments[SMOOTH_ID] = addon_fragment(smooth_xml)
    print("plugin %s %s (built)" % (SMOOTH_ID, smooth_version))

    built = {}
    for addon_id in REPO_ADDONS:
        addon_dir = ROOT / addon_id
        text = (addon_dir / "addon.xml").read_text(encoding="utf-8")
        found_id, version = addon_id_version(text)
        if found_id != addon_id:
            fail("%s has id %s" % (addon_dir, found_id))
        filename = "%s-%s.zip" % (addon_id, version)
        dest = addon_dir / filename
        zip_directory(addon_dir, dest)
        packed = read_zip_addon_xml(dest.read_bytes(), addon_id)
        if addon_fragment(packed) != addon_fragment(text):
            fail("%s zip addon.xml does not match the source" % addon_id)
        remove_stale_zips(addon_dir, addon_id, filename)
        fragments[addon_id] = addon_fragment(text)
        built[addon_id] = dest
        print("repo %s %s" % (addon_id, version))

    ordered = [fragments[addon_id] for addon_id in sorted(fragments)]
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<addons>\n" + "\n".join(ordered) + "\n</addons>\n"
    )
    data = document.encode("utf-8")
    # One parse proves every embedded addon element is well formed.
    root = ET.fromstring(data)
    ids = [element.attrib["id"] for element in root.findall("addon")]
    expected = sorted([PRIME_ID, SMOOTH_ID, *REPO_ADDONS])
    if ids != expected:
        fail("addons.xml lists %s" % ids)
    write_bytes(ROOT / "addons.xml", data)
    digest = hashlib.md5(data).hexdigest()
    write_bytes(ROOT / "addons.xml.md5", (digest + "\n").encode("utf-8"))

    repo_zip = built["repository.scottywins"]
    root_zip = ROOT / repo_zip.name
    write_bytes(root_zip, repo_zip.read_bytes())
    for stale in ROOT.glob("repository.scottywins-*.zip"):
        if stale.name != repo_zip.name:
            stale.unlink()
    write_index(repo_zip.name, root_zip.stat().st_size)
    (ROOT / ".nojekyll").touch()

    print("addons.xml md5 %s" % digest)
    print("install zip %s" % repo_zip.name)
    for addon_id in expected:
        element = root.find("addon[@id='%s']" % addon_id)
        print("listed %s %s" % (addon_id, element.attrib["version"]))

    if args.update_legacy:
        update_legacy(primescene, smoothflix, built)


if __name__ == "__main__":
    main()
