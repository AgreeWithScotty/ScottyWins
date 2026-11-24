#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild the ScottyWins Kodi repository from the source checkouts.

The public channel reads PrimeScene release and SmoothFlix main. It does
not read PrimeScene main. Pass --testing-primescene to publish the separate
/testing channel from a PrimeScene checkout (the testing branch, or main
when that branch is absent): Smooth Prime and repository.scottywins.testing
only.

The testing repository datadir is the GitHub Release download prefix.
Kodi requests datadir + "<id>/<id>-<version>.zip", which is one release
asset whose tag is the add-on id. Icon and fanart stay on the Pages
artdir. The Pages copy of each zip is still written for clients whose
installed testing repository has not updated yet.

The script is deterministic: zip entries use a fixed timestamp, mode, and
file order, and copied release zips are written through unchanged. Only the
latest version of each add-on is kept.

Kodi's HTTP directory parser (HTTPDirectory.cpp) keeps an <a href> only when
the link text equals the href. A href that starts with "//" is protocol-relative:
it names a host, so the zip never appears in the folder. The indexes are Apache
HTML listings whose href and label are the zip filename. Kodi appends that
filename to the directory path, which is why the source URL needs its trailing
slash (Pages redirects the bare URL onto that form).
"""

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = "https://agreewithscotty.github.io/ScottyWins"
REPO_ADDONS = (
    "repository.primescene",
    "repository.scottywins",
    "repository.smoothflix",
)
# The test channel is a separate Pages directory. Public cleanup must leave
# it alone, and it is not one of REPO_ADDONS, so it never enters addons.xml.
TESTING_DIR = "testing"
TESTING_PLUGIN = "plugin.video.primeinterface"
TESTING_REPO = "repository.scottywins.testing"
# Kodi Omega builds the zip URL in AddonInfoBuilder as
# datadir + "<id>/<id>-<version>.zip". With this prefix the tag is the
# add-on id and the asset name is the zip. An absolute <path> is not used:
# AddFileToFolder appends it to the datadir, and ResolvePathAndHash rejects
# a zip that is not under the datadir.
RELEASE_DOWNLOAD_DATADIR = "https://github.com/AgreeWithScotty/ScottyWins/releases/download/"
# artdir defaults to datadir. Keep artwork on Pages, where the paths in
# <assets> already point (including fanart paths that contain slashes).
TESTING_ARTDIR = PAGES + "/testing/"
# File manager install link published before the datadir change.
TESTING_KEEP_ROOT_ZIPS = ("repository.scottywins.testing-1.0.0.zip",)
PROTECTED_ROOT_DIRS = {TESTING_DIR}
PRIME_RAW = "https://raw.githubusercontent.com/AgreeWithScotty/PrimeScene/%s/"
# Public Smooth Prime comes from this branch only. main is where new builds
# land first; publishing it would skip the test channel.
PRIME_PUBLIC_BRANCH = "release"
# Prebuilt zips live under packages/ in PrimeScene and next to the add-on
# folder in SmoothFlix. Repository add-ons are authored in this repo: their
# addon.xml versions are the source of truth, and upstream repository.*
# entries are ignored so a daily rebuild cannot rename or roll them back.
KEEP_ROOT_ZIPS = ("repository.scottywins-1.0.0.zip",)
SOURCES = (
    {
        "raw": PRIME_RAW % PRIME_PUBLIC_BRANCH,
        "zip_prefix": "packages/",
    },
    {
        "raw": "https://raw.githubusercontent.com/AgreeWithScotty/SmoothFlix/main/",
        "zip_prefix": "",
    },
)
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIXES = (".zip", ".pyc", ".pyo", ".tmp")
# Same expression Kodi compiles in CHTTPDirectory::GetDirectory.
# Kodi's CRegExp sets PCRE_DOTALL, so "." crosses newlines.
KODI_LINK = re.compile(
    r'<a href="([^"]*)"[^>]*>\s*(.*?)\s*</a>(.+?)(?=<a|</tr|$)',
    re.IGNORECASE | re.DOTALL,
)


def fail(message):
    print("error: %s" % message, file=sys.stderr)
    raise SystemExit(1)


def _curl_parts(url):
    """Split an http(s) URL the way Kodi's CURL::Parse does.

    The filename is the path without its leading slash. Query and fragment
    are not used for repository datadir URLs.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment:
        return None
    path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
    return parsed.scheme, parsed.netloc, path


def kodi_add_slash_at_end(folder):
    """Mirror URIUtils::AddSlashAtEnd for the datadir parent check."""
    parts = _curl_parts(folder) if "://" in folder else None
    if parts is not None:
        scheme, host, path = parts
        if path and path != folder:
            path = kodi_add_slash_at_end(path)
            return "%s://%s/%s" % (scheme, host, path)
        return folder
    if folder.endswith("/") or folder.endswith("\\"):
        return folder
    return folder + "/"


def kodi_add_file_to_folder(folder, filename):
    """Mirror URIUtils::AddFileToFolder. An absolute filename is appended."""
    parts = _curl_parts(folder) if "://" in folder else None
    if parts is not None:
        scheme, host, path = parts
        if path != folder:
            new_file = kodi_add_file_to_folder(path, filename)
            base = "%s://%s/" % (scheme, host)
            if new_file.startswith("/") and base.endswith("/"):
                return base[:-1] + new_file
            return base + new_file
    result = folder
    if result and not result.endswith("/") and not result.endswith("\\"):
        result += "/"
    if filename.startswith("/") or filename.startswith("\\"):
        result += filename[1:]
    else:
        result += filename
    if not (len(folder) > 1 and folder[1] == ":" and folder[0].isalpha()):
        result = result.replace("\\", "/")
    return result


def kodi_zip_url(datadir, addon_id, version):
    """The zip URL CAddonInfoBuilder stores when metadata has no <path>."""
    filename = "%s-%s.zip" % (addon_id, version)
    return kodi_add_file_to_folder(kodi_add_file_to_folder(datadir, addon_id), filename)


def kodi_path_has_parent(path, parent):
    """Mirror URIUtils::PathHasParent without special:// translation."""
    if not parent:
        return False
    if path == parent:
        return True
    parent = kodi_add_slash_at_end(parent)
    return path.startswith(parent)


def check_kodi_url_rules():
    """Lock the Omega URL rules this feed depends on."""
    addon_id = "plugin.video.primeinterface"
    version = "1.2.3"
    expected = "%s%s/%s-%s.zip" % (RELEASE_DOWNLOAD_DATADIR, addon_id, addon_id, version)
    url = kodi_zip_url(RELEASE_DOWNLOAD_DATADIR, addon_id, version)
    if url != expected:
        fail("kodi zip url %s != %s" % (url, expected))
    bare = RELEASE_DOWNLOAD_DATADIR.rstrip("/")
    if kodi_zip_url(bare, addon_id, version) != expected:
        fail("datadir without a trailing slash did not join to %s" % expected)
    if not kodi_path_has_parent(url, RELEASE_DOWNLOAD_DATADIR):
        fail("release zip is not under the release datadir")
    if not kodi_path_has_parent(url, bare):
        fail("release zip is not under the release datadir without a slash")
    # <path>https://...</path> is appended to the datadir path. It does not
    # replace it, so it cannot name a GitHub Release from a Pages datadir.
    joined = kodi_add_file_to_folder(TESTING_ARTDIR, url)
    if joined != TESTING_ARTDIR + url:
        fail("absolute <path> joined as %s" % joined)
    if kodi_path_has_parent(url, TESTING_ARTDIR):
        fail("release zip was treated as a child of the Pages datadir")
    if not kodi_path_has_parent(joined, TESTING_ARTDIR):
        fail("joined absolute <path> escaped the Pages datadir")
    icon = kodi_add_file_to_folder(kodi_add_file_to_folder(TESTING_ARTDIR, addon_id), "icon.png")
    if icon != TESTING_ARTDIR + addon_id + "/icon.png":
        fail("icon url %s" % icon)
    fanart_rel = "resources/skins/Default/media/splash.png"
    fanart = kodi_add_file_to_folder(
        kodi_add_file_to_folder(TESTING_ARTDIR, addon_id), fanart_rel
    )
    if fanart != TESTING_ARTDIR + addon_id + "/" + fanart_rel:
        fail("fanart url %s" % fanart)
    if kodi_path_has_parent(fanart, RELEASE_DOWNLOAD_DATADIR):
        fail("fanart url was placed on the release datadir")
    return url


def repository_dirs(fragment):
    """info, checksum, datadir, and artdir from one repository add-on element."""
    root = ET.fromstring(fragment.encode("utf-8"))
    for ext in root.findall("extension"):
        if ext.attrib.get("point") != "xbmc.addon.repository":
            continue
        found = {}
        for name in ("info", "checksum", "datadir", "artdir"):
            node = ext.find("dir/%s" % name)
            found[name] = node.text.strip() if node is not None and node.text else ""
        return found
    return None


def metadata_path_text(fragment):
    root = ET.fromstring(fragment.encode("utf-8"))
    for ext in root.findall("extension"):
        if ext.attrib.get("point") not in ("xbmc.addon.metadata", "kodi.addon.metadata"):
            continue
        node = ext.find("path")
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    return ""


def metadata_assets(fragment):
    root = ET.fromstring(fragment.encode("utf-8"))
    found = {}
    for ext in root.findall("extension"):
        if ext.attrib.get("point") not in ("xbmc.addon.metadata", "kodi.addon.metadata"):
            continue
        assets = ext.find("assets")
        if assets is None:
            continue
        for child in list(assets):
            if child.text and child.text.strip():
                found[child.tag] = child.text.strip()
    return found


def release_asset_from_datadir(datadir, addon_id, version):
    """Return (zip URL, tag, filename) for one add-on under the release datadir."""
    url = kodi_zip_url(datadir, addon_id, version)
    if not kodi_path_has_parent(url, datadir):
        fail("Kodi zip URL %s is not under datadir %s" % (url, datadir))
    if not url.startswith(datadir):
        fail("Kodi zip URL %s does not start with %s" % (url, datadir))
    rest = url[len(datadir):]
    expected = "%s/%s-%s.zip" % (addon_id, addon_id, version)
    if rest != expected:
        fail("Kodi zip URL %s is not %s" % (url, datadir + expected))
    tag, filename = rest.split("/", 1)
    return url, tag, filename


def assert_testing_release_layout(fragment):
    dirs = repository_dirs(fragment)
    if not dirs:
        fail("testing repository add-on has no repository extension")
    if dirs["datadir"] != RELEASE_DOWNLOAD_DATADIR:
        fail("testing datadir is %s" % dirs["datadir"])
    if dirs["artdir"] != TESTING_ARTDIR:
        fail("testing artdir is %s" % dirs["artdir"])
    if dirs["info"] != TESTING_ARTDIR + "addons.xml":
        fail("testing info URL is %s" % dirs["info"])
    if dirs["checksum"] != TESTING_ARTDIR + "addons.xml.md5":
        fail("testing checksum URL is %s" % dirs["checksum"])
    return dirs


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


def listed_addons(addons_xml):
    """Non-repository add-ons published by a source repo's addons.xml."""
    if not addons_xml.is_file():
        fail("missing %s" % addons_xml)
    root = ET.parse(addons_xml).getroot()
    found = []
    for element in root.findall("addon"):
        addon_id = element.attrib.get("id", "")
        if addon_id.startswith("repository."):
            continue
        if not addon_id or "version" not in element.attrib:
            fail("addons.xml entry is missing an id or version")
        found.append((addon_id, element.attrib["version"]))
    return found


def load_release_zip(repo, addon_id, version, raw_base, zip_prefix):
    filename = "%s-%s.zip" % (addon_id, version)
    relative = "%s%s/%s" % (zip_prefix, addon_id, filename)
    local = repo / relative
    if local.is_file():
        data = local.read_bytes()
        origin = str(local)
    else:
        url = raw_base + relative
        request = urllib.request.Request(url, headers={"User-Agent": "scottywins-build"})
        with urllib.request.urlopen(request, timeout=180) as response:
            data = response.read()
        origin = url
    text = read_zip_addon_xml(data, addon_id)
    found_id, found_version = addon_id_version(text)
    if found_id != addon_id or found_version != version:
        fail("%s is %s %s" % (origin, found_id, found_version))
    return data, text


def collect_source(repo, raw_base, zip_prefix, include_ids=None):
    """Yield (kind, id, version, payload, addon.xml text) for one checkout.

    kind is "zip" (payload is bytes) or "source" (payload is the add-on directory).
    Every non-repository addons.xml entry is included. A directory with its own
    addon.xml is built from that tree; otherwise the published zip is copied.
    include_ids limits the result to those add-on ids. The public channel
    leaves it unset.
    """
    listed = listed_addons(repo / "addons.xml")
    seen = set()
    items = []
    for addon_id, version in listed:
        seen.add(addon_id)
        if include_ids is not None and addon_id not in include_ids:
            continue
        addon_xml = repo / addon_id / "addon.xml"
        if addon_xml.is_file():
            text = addon_xml.read_text(encoding="utf-8")
            found_id, found_version = addon_id_version(text)
            if found_id != addon_id:
                fail("%s has id %s" % (addon_xml, found_id))
            if found_version != version:
                fail("%s is %s in addon.xml and %s in addons.xml" % (addon_id, found_version, version))
            items.append(("source", addon_id, found_version, repo / addon_id, text))
        else:
            data, text = load_release_zip(repo, addon_id, version, raw_base, zip_prefix)
            items.append(("zip", addon_id, version, data, text))
    for child in sorted(repo.iterdir()):
        if not child.is_dir() or child.name in seen or child.name.startswith("repository."):
            continue
        if include_ids is not None and child.name not in include_ids:
            continue
        addon_xml = child / "addon.xml"
        if not addon_xml.is_file():
            continue
        text = addon_xml.read_text(encoding="utf-8")
        found_id, version = addon_id_version(text)
        if found_id != child.name:
            fail("%s has id %s" % (addon_xml, found_id))
        items.append(("source", found_id, version, child, text))
    if include_ids is not None:
        missing = sorted(set(include_ids) - {item[1] for item in items})
        if missing:
            fail("%s is missing %s" % (repo, ", ".join(missing)))
    return items


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
    """Return hrefs Kodi's HTTP directory parser would keep.

    Mirrors CHTTPDirectory::GetDirectory: one leading slash is stripped, then
    the link text has to equal that href. "//file.zip" is left as "/file.zip"
    and only matches a label that still has the slash; a plain "file.zip"
    matches itself.
    """
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


def write_index(zip_name, size_bytes, directory=None):
    """Write an Apache HTML listing Kodi parses. The href is the zip filename."""
    if directory is None:
        directory = ROOT
    if directory == ROOT:
        index_of = "/ScottyWins"
        folder_url = PAGES + "/"
    else:
        rel = directory.relative_to(ROOT).as_posix().strip("/")
        index_of = "/ScottyWins/" + rel
        folder_url = "%s/%s/" % (PAGES, rel)
    if zip_name.startswith(("/", ".")) or "/" in zip_name or "\\" in zip_name:
        fail("index href must be a plain filename, not %s" % zip_name)
    kilobytes = max(1, size_bytes // 1024)
    # Same table shape as the apache-html listing in Kodi's HTTPDirectory tests.
    # Column-sort links are kept out of the result because their labels differ
    # from their hrefs. The zip row's label and href are both the filename.
    html = (
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">\n'
        "<html>\n"
        " <head>\n"
        "  <title>Index of %s</title>\n"
        " </head>\n"
        " <body>\n"
        "<h1>Index of %s</h1>\n"
        "  <table>\n"
        '   <tr><th valign="top">&nbsp;</th><th><a href="?C=N;O=D">Name</a></th>'
        '<th><a href="?C=M;O=A">Last modified</a></th>'
        '<th><a href="?C=S;O=A">Size</a></th>'
        '<th><a href="?C=D;O=A">Description</a></th></tr>\n'
        '   <tr><th colspan="5"><hr></th></tr>\n'
        '<tr><td valign="top">&nbsp;</td><td><a href="%s">%s</a></td>'
        '<td align="right">1980-01-01 00:00  </td>'
        '<td align="right"> %dK</td><td>&nbsp;</td></tr>\n'
        '   <tr><th colspan="5"><hr></th></tr>\n'
        "</table>\n"
        "</body></html>\n"
    ) % (index_of, index_of, zip_name, zip_name, kilobytes)
    targets = kodi_link_targets(html)
    if targets != [zip_name]:
        fail("index.html is not readable by Kodi's HTTP directory parser: %r" % (targets,))
    resolved = urllib.parse.urljoin(folder_url, zip_name)
    if resolved != folder_url + zip_name:
        fail("index.html href does not resolve under %s: %s" % (folder_url, resolved))
    # Kodi concatenates the href onto the directory path from GetFileName().
    # Pages serves the folder with a trailing slash, and that slash is the
    # separator this join needs.
    directory_path = folder_url.split("://", 1)[1].split("/", 1)[1]
    kodi_url = "https://agreewithscotty.github.io/" + directory_path + targets[0]
    if kodi_url != resolved:
        fail("Kodi would request %s instead of %s" % (kodi_url, resolved))
    write_bytes(directory / "index.html", html.encode("utf-8"))


def addon_blocks(text):
    """Map add-on id to the raw <addon> element, whitespace included."""
    blocks = {}
    position = 0
    while True:
        start = text.find("<addon ", position)
        if start < 0:
            break
        end = text.find("</addon>", start)
        if end < 0:
            fail("addons.xml has an unclosed addon element")
        end += len("</addon>")
        element = text[start:end]
        addon_id = ET.fromstring(element).attrib.get("id")
        if not addon_id:
            fail("addons.xml entry is missing an id")
        blocks[addon_id] = element
        position = end
    return blocks


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
    # text[:start] already holds this line's indent. Prefixing the opening
    # tag again is what turned two spaces into four.
    lines = fragment.split("\n")
    block = "\n".join(
        [lines[0]] + [indent + line if line else line for line in lines[1:]]
    )
    return text[:start] + block + text[end:]


def write_legacy_index(checkout, addon_id, fragment):
    xml_path = checkout / "addons.xml"
    original = xml_path.read_text(encoding="utf-8")
    updated = replace_addon_block(original, addon_id, fragment)
    old_blocks = addon_blocks(original)
    new_blocks = addon_blocks(updated)
    for kept_id, block in old_blocks.items():
        if kept_id == addon_id:
            continue
        if new_blocks.get(kept_id) != block:
            fail("legacy update rewrote %s" % kept_id)
    tag = '<addon id="%s"' % addon_id
    new_at = updated.find(tag)
    old_at = original.find(tag)
    new_indent = updated[updated.rfind("\n", 0, new_at) + 1:new_at]
    old_indent = original[original.rfind("\n", 0, old_at) + 1:old_at]
    if new_indent != old_indent:
        fail("%s opening indent changed from %r to %r" % (addon_id, old_indent, new_indent))
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
    prime_dest = primescene / "packages" / prime_id / prime_zip.name
    prime_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(prime_zip, prime_dest)
    shutil.copyfile(ROOT / prime_id / "addon.xml", primescene / "packages" / prime_id / "addon.xml")
    write_legacy_index(primescene, prime_id, addon_fragment((ROOT / prime_id / "addon.xml").read_text(encoding="utf-8")))

    shutil.copyfile(smooth_zip, smoothflix / smooth_id / smooth_zip.name)
    for name in ("addon.xml", "changelog.txt"):
        shutil.copyfile(ROOT / smooth_id / name, smoothflix / smooth_id / name)
    write_legacy_index(smoothflix, smooth_id, addon_fragment((ROOT / smooth_id / "addon.xml").read_text(encoding="utf-8")))


def pack_repo_addon(addon_id, dest_dir):
    """Zip an authored repository add-on. The source tree is ROOT/addon_id."""
    addon_dir = ROOT / addon_id
    text = (addon_dir / "addon.xml").read_text(encoding="utf-8")
    found_id, version = addon_id_version(text)
    if found_id != addon_id:
        fail("%s has id %s" % (addon_dir, found_id))
    filename = "%s-%s.zip" % (addon_id, version)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    zip_directory(addon_dir, dest)
    packed = read_zip_addon_xml(dest.read_bytes(), addon_id)
    if addon_fragment(packed) != addon_fragment(text):
        fail("%s zip addon.xml does not match the source" % addon_id)
    remove_stale_zips(dest_dir, addon_id, filename)
    return version, dest, addon_fragment(text)


def write_addons_xml(directory, fragments, required_ids=()):
    ordered = [fragments[addon_id] for addon_id in sorted(fragments)]
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        "<addons>\n" + "\n".join(ordered) + "\n</addons>\n"
    )
    data = document.encode("utf-8")
    # One parse proves every embedded addon element is well formed.
    root = ET.fromstring(data)
    ids = [element.attrib["id"] for element in root.findall("addon")]
    expected = sorted(fragments)
    if ids != expected:
        fail("addons.xml lists %s" % ids)
    for addon_id in required_ids:
        if addon_id not in ids:
            fail("addons.xml is missing %s" % addon_id)
    write_bytes(directory / "addons.xml", data)
    digest = hashlib.md5(data).hexdigest()
    write_bytes(directory / "addons.xml.md5", (digest + "\n").encode("utf-8"))
    return root, digest, expected


def git_branch(repo):
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""
    if out == "HEAD":
        return ""
    return out


def version_key(version):
    """Order dotted Kodi add-on versions. Numeric pieces compare as integers."""
    parts = []
    for part in version.split("."):
        if part.isdigit():
            parts.append((0, int(part)))
        else:
            parts.append((1, part))
    return tuple(parts)


def published_version(addon_id, directory=None):
    xml_path = (directory or ROOT) / "addons.xml"
    if not xml_path.is_file():
        return None
    element = ET.parse(xml_path).getroot().find("addon[@id='%s']" % addon_id)
    if element is None:
        return None
    return element.attrib.get("version")


def build_testing_channel(primescene):
    """Publish Smooth Prime and the testing repo add-on under /testing.

    The checkout's branch selects the raw zip URL, so the zip matches
    addons.xml. A checkout that is not on testing is reported; the workflow
    only passes main in that case, and only when the testing branch is absent.
    """
    primescene = primescene.resolve()
    branch = git_branch(primescene)
    if not branch:
        fail(
            "%s is not on a named branch. Clone --branch testing, or --branch main "
            "when the testing branch does not exist yet." % primescene
        )
    if branch != "testing":
        print(
            "note: PrimeScene testing branch is not checked out (branch is %s). "
            "Building the test channel from %s. This fallback is for a missing "
            "testing branch; push to testing before relying on the test channel."
            % (branch, branch),
            file=sys.stderr,
        )
    raw = PRIME_RAW % branch
    output = ROOT / TESTING_DIR
    fragments = {}
    plugin_version = None
    for kind, addon_id, version, payload, text in collect_source(
        primescene, raw, "packages/", include_ids={TESTING_PLUGIN}
    ):
        if addon_id in fragments:
            fail("%s is published by more than one source" % addon_id)
        filename = "%s-%s.zip" % (addon_id, version)
        dest_dir = output / addon_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        if kind == "zip":
            write_bytes(dest, payload)
            print("testing addon %s %s (%s bytes, copied)" % (addon_id, version, len(payload)))
        else:
            zip_directory(payload, dest)
            packed = read_zip_addon_xml(dest.read_bytes(), addon_id)
            if addon_id_version(packed) != (addon_id, version):
                fail("%s zip does not match its addon.xml" % addon_id)
            print("testing addon %s %s (built)" % (addon_id, version))
        remove_stale_zips(dest_dir, addon_id, filename)
        fragments[addon_id] = addon_fragment(text)
        plugin_version = version

    public_version = published_version(TESTING_PLUGIN)
    if public_version and version_key(plugin_version) <= version_key(public_version):
        print(
            "warning: %s testing version %s is not newer than public %s. "
            "Kodi offers an update only when the testing version is greater. "
            "Bump plugin.video.primeinterface on the PrimeScene testing branch. "
            "See TESTING.md."
            % (TESTING_PLUGIN, plugin_version, public_version),
            file=sys.stderr,
        )

    # The File manager install zip and the package 1.0.0 clients download when
    # they update this repository. Kodi 1.0.1 reads the zip from the release
    # datadir; this Pages copy is the fallback.
    version, dest, fragment = pack_repo_addon(TESTING_REPO, output / TESTING_REPO)
    fragments[TESTING_REPO] = fragment
    print("testing repo %s %s" % (TESTING_REPO, version))
    install_zip = output / dest.name
    write_bytes(install_zip, dest.read_bytes())
    for stale in output.glob("%s-*.zip" % TESTING_REPO):
        if stale.name != dest.name and stale.name not in TESTING_KEEP_ROOT_ZIPS:
            stale.unlink()

    for child in list(output.iterdir()) if output.is_dir() else []:
        if child.is_dir() and child.name not in {TESTING_PLUGIN, TESTING_REPO}:
            shutil.rmtree(child)

    check_kodi_url_rules()
    dirs = assert_testing_release_layout(fragments[TESTING_REPO])
    listed_versions = (
        (TESTING_PLUGIN, plugin_version),
        (TESTING_REPO, version),
    )
    for addon_id, addon_version in listed_versions:
        if metadata_path_text(fragments[addon_id]):
            fail(
                "%s metadata <path> is joined onto the datadir and also moves icon and fanart"
                % addon_id
            )
        zip_url, _tag, filename = release_asset_from_datadir(
            dirs["datadir"], addon_id, addon_version
        )
        print("testing kodi zip %s" % zip_url)
        print("testing pages zip %s/%s/%s/%s" % (PAGES, TESTING_DIR, addon_id, filename))
        for kind, relative in metadata_assets(fragments[addon_id]).items():
            asset_url = kodi_add_file_to_folder(
                kodi_add_file_to_folder(dirs["artdir"], addon_id), relative
            )
            prefix = "%s%s/" % (dirs["artdir"], addon_id)
            if not asset_url.startswith(prefix) or kodi_path_has_parent(asset_url, dirs["datadir"]):
                fail("%s %s URL %s left the Pages artwork directory" % (addon_id, kind, asset_url))
            print("testing %s %s" % (kind, asset_url))

    root, digest, expected = write_addons_xml(
        output, fragments, (TESTING_PLUGIN, TESTING_REPO)
    )
    write_index(dest.name, install_zip.stat().st_size, directory=output)
    print("testing addons.xml md5 %s" % digest)
    print("testing install zip %s/%s/%s" % (PAGES, TESTING_DIR, dest.name))
    for addon_id in expected:
        element = root.find("addon[@id='%s']" % addon_id)
        print("testing listed %s %s" % (addon_id, element.attrib["version"]))


def build_public(primescene, smoothflix, update_legacy_repos):
    primescene = primescene.resolve()
    smoothflix = smoothflix.resolve()
    branch = git_branch(primescene)
    if branch != PRIME_PUBLIC_BRANCH:
        fail(
            "public channel reads PrimeScene %s, but %s is on %s. "
            "Not using main. Published files were left unchanged."
            % (PRIME_PUBLIC_BRANCH, primescene, branch or "no branch")
        )

    fragments = {}
    checkouts = (
        (primescene, SOURCES[0]),
        (smoothflix, SOURCES[1]),
    )
    for repo, source in checkouts:
        for kind, addon_id, version, payload, text in collect_source(
            repo, source["raw"], source["zip_prefix"]
        ):
            if addon_id in fragments:
                fail("%s is published by more than one source" % addon_id)
            filename = "%s-%s.zip" % (addon_id, version)
            dest_dir = ROOT / addon_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / filename
            if kind == "zip":
                write_bytes(dest, payload)
                print("addon %s %s (%s bytes, copied)" % (addon_id, version, len(payload)))
            else:
                zip_directory(payload, dest)
                packed = read_zip_addon_xml(dest.read_bytes(), addon_id)
                if addon_id_version(packed) != (addon_id, version):
                    fail("%s zip does not match its addon.xml" % addon_id)
                print("addon %s %s (built)" % (addon_id, version))
            remove_stale_zips(dest_dir, addon_id, filename)
            fragments[addon_id] = addon_fragment(text)

    keep_dirs = set(fragments)
    for child in list(ROOT.iterdir()):
        if not child.is_dir() or child.name in keep_dirs or child.name.startswith("."):
            continue
        if child.name in PROTECTED_ROOT_DIRS:
            continue
        if (child / "addon.xml").is_file():
            continue
        if any(child.glob("*.zip")):
            shutil.rmtree(child)

    built = {}
    for addon_id in REPO_ADDONS:
        version, dest, fragment = pack_repo_addon(addon_id, ROOT / addon_id)
        fragments[addon_id] = fragment
        built[addon_id] = dest
        print("repo %s %s" % (addon_id, version))

    root, digest, expected = write_addons_xml(ROOT, fragments, REPO_ADDONS)

    repo_zip = built["repository.scottywins"]
    root_zip = ROOT / repo_zip.name
    write_bytes(root_zip, repo_zip.read_bytes())
    keep_root = {repo_zip.name, *KEEP_ROOT_ZIPS}
    for stale in ROOT.glob("repository.scottywins-*.zip"):
        if stale.name not in keep_root:
            stale.unlink()
    write_index(repo_zip.name, root_zip.stat().st_size)
    (ROOT / ".nojekyll").touch()

    print("addons.xml md5 %s" % digest)
    print("install zip %s" % repo_zip.name)
    for addon_id in expected:
        element = root.find("addon[@id='%s']" % addon_id)
        print("listed %s %s" % (addon_id, element.attrib["version"]))

    if update_legacy_repos:
        update_legacy(primescene, smoothflix, built)


def main():
    parser = argparse.ArgumentParser(description="Rebuild the ScottyWins Kodi repository.")
    parser.add_argument("--primescene", type=Path)
    parser.add_argument("--smoothflix", type=Path)
    parser.add_argument(
        "--testing-primescene",
        type=Path,
        help="PrimeScene checkout for the /testing channel. Branch testing is the test channel; any other branch is reported and used only as the missing-branch fallback.",
    )
    parser.add_argument(
        "--update-legacy",
        action="store_true",
        help="Also write the bumped repository add-ons into the source checkouts.",
    )
    parser.add_argument(
        "--check-kodi-urls",
        action="store_true",
        help="Check Kodi zip and artwork URL construction, then exit.",
    )
    args = parser.parse_args()
    if args.check_kodi_urls:
        print(check_kodi_url_rules())
        return
    if bool(args.primescene) != bool(args.smoothflix):
        fail("public build needs both --primescene and --smoothflix")
    if not args.primescene and not args.testing_primescene:
        fail("pass --primescene and --smoothflix, or --testing-primescene")
    if args.primescene:
        build_public(args.primescene, args.smoothflix, args.update_legacy)
    if args.testing_primescene:
        build_testing_channel(args.testing_primescene)


if __name__ == "__main__":
    main()
